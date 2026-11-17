import ee
import pandas as pd
import time
import google.auth

# ==========================================
# CONFIGURAÇÕES DO PROJETO
# ==========================================
PROJECT_ID = 'agrovale-gcp' 
BQ_TABLE = 'agrovale_dataset.bronze_15d' 

# 1. Autenticação via Service Account (Cloud Run)
# O Cloud Run pega as credenciais automaticamente da Conta de Serviço anexada ao Job.
print("Autenticando no Google Earth Engine...")
credentials, project = google.auth.default(
    scopes=[
        "https://www.googleapis.com/auth/earthengine",
        "https://www.googleapis.com/auth/cloud-platform"
    ]
)
ee.Initialize(credentials=credentials, project=PROJECT_ID)

# ==========================================
# 1. DEFINIR TALHÕES DA FAZENDA
# ==========================================
talhoes_milho = ee.FeatureCollection([
    ee.Feature(ee.Geometry.Polygon([[
        [-51.0650, -17.6550], [-51.0650, -17.7350], 
        [-50.9650, -17.7350], [-50.9650, -17.6550], 
        [-51.0650, -17.6550]
    ]]), {'talhao_id': 'Talhao_Milho_01_Sequeiro'}),
    
    ee.Feature(ee.Geometry.Polygon([[
        [-51.0650, -17.6550], [-51.0650, -17.7350], 
        [-50.9650, -17.7350], [-50.9650, -17.6550], 
        [-51.0650, -17.6550]
    ]]), {'talhao_id': 'Talhao_Milho_02_Pivo'})
])

def garantir_id(feat):
    id_atual = ee.Algorithms.If(feat.get('talhao_id'), feat.get('talhao_id'), feat.get('system:index'))
    return feat.set('talhao_id', id_atual)

talhoes_milho = talhoes_milho.map(garantir_id)

# ==========================================
# 2. MÁSCARA DE LAVOURA TEMPORÁRIA
# ==========================================
mapbiomas = ee.Image('projects/mapbiomas-public/assets/brazil/lulc/collection9/mapbiomas_collection90_integration_v1')
uso_solo = mapbiomas.select('classification_2023')
mascara_lavoura = uso_solo.eq(39).Or(uso_solo.eq(41))

# ==========================================
# 3. CONFIGURAÇÃO QUINZENAL E EXTRAÇÃO
# ==========================================
ANO_SAFRA_INICIO = 2018
ANO_SAFRA_FIM = 2025
PASSO_DIAS = 15

CLASSIFICACAO_ENSO = {
    '2018/2019': 'El Nino (Fraco)',
    '2019/2020': 'Neutro (Normal)',
    '2020/2021': 'La Nina',
    '2021/2022': 'La Nina',
    '2022/2023': 'Neutro / Transicao',
    '2023/2024': 'El Nino (Forte)',
    '2024/2025': 'Neutro / La Nina Fraca',
    '2025/2026': 'Neutro (Normal)'
}

def imagem_vazia(nomes_bandas):
    bandas = [ee.Image.constant(0).selfMask().rename(nome) for nome in nomes_bandas]
    return ee.Image.cat(bandas)

def prep_s2(img):
    scl = img.select('SCL')
    mask_final = scl.eq(4).Or(scl.eq(5)).Or(scl.eq(6)).And(mascara_lavoura)
    scaled = img.divide(10000)
    ndvi = scaled.normalizedDifference(['B8', 'B4']).rename('NDVI')
    evi = scaled.expression(
        '2.5 * ((NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1))', {
            'NIR': scaled.select('B8'),
            'RED': scaled.select('B4'),
            'BLUE': scaled.select('B2')
        }).rename('EVI')
    return img.addBands([ndvi, evi]).updateMask(mask_final).select(['NDVI', 'EVI'])

def prep_modis(img):
    lst_dia = img.select('LST_Day_1km').multiply(0.02).subtract(273.15).rename('Temp_Dia_C')
    lst_noite = img.select('LST_Night_1km').multiply(0.02).subtract(273.15).rename('Temp_Noite_C')
    return img.addBands([lst_dia, lst_noite]).select(['Temp_Dia_C', 'Temp_Noite_C'])

DATA_GLOBAL_INI = f'{ANO_SAFRA_INICIO}-10-01'
DATA_GLOBAL_FIM = f'{ANO_SAFRA_FIM + 1}-10-01'

col_s2 = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
          .filterBounds(talhoes_milho)
          .filterDate(DATA_GLOBAL_INI, DATA_GLOBAL_FIM)
          .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 70))
          .map(prep_s2))

col_chirps = (ee.ImageCollection('UCSB-CHG/CHIRPS/DAILY')
              .filterBounds(talhoes_milho)
              .filterDate(DATA_GLOBAL_INI, DATA_GLOBAL_FIM))

col_modis = (ee.ImageCollection('MODIS/061/MOD11A2')
             .filterBounds(talhoes_milho)
             .filterDate(DATA_GLOBAL_INI, DATA_GLOBAL_FIM)
             .map(prep_modis))

def extrair_semestre_safra(ano_inicio_safra, semestre):
    safra_label = f"{ano_inicio_safra}/{ano_inicio_safra + 1}"
    status_enso = CLASSIFICACAO_ENSO.get(safra_label, 'Nao Classificado')

    if semestre == 1:
        start = ee.Date.fromYMD(ano_inicio_safra, 10, 1)
        offset_quinzena = 0
    else:
        start = ee.Date.fromYMD(ano_inicio_safra + 1, 4, 1)
        offset_quinzena = 12

    lista_indices = ee.List.sequence(0, 11)

    def processar_quinzena(idx):
        idx_num = ee.Number(idx)
        d1 = start.advance(idx_num.multiply(PASSO_DIAS), 'day')
        d2 = d1.advance(PASSO_DIAS, 'day')

        s2_sub = col_s2.filterDate(d1, d2)
        s2_img = ee.Image(ee.Algorithms.If(
            s2_sub.size().gt(0), s2_sub.median(), imagem_vazia(['NDVI', 'EVI'])
        ))

        chirps_sub = col_chirps.filterDate(d1, d2)
        chuva_img = chirps_sub.sum().rename('Chuva_mm')
        dias_secos_img = chirps_sub.map(lambda i: i.lt(2)).sum().rename('Dias_Secos')

        modis_sub = col_modis.filterDate(d1, d2)
        temp_img = ee.Image(ee.Algorithms.If(
            modis_sub.size().gt(0), modis_sub.mean(), imagem_vazia(['Temp_Dia_C', 'Temp_Noite_C'])
        ))

        stack = ee.Image.cat([s2_img, chuva_img, dias_secos_img, temp_img])
        stats = stack.reduceRegions(
            collection=talhoes_milho,
            reducer=ee.Reducer.mean(),
            scale=10,
            tileScale=4
        )

        return stats.map(lambda f: f.set({
            'safra': safra_label,
            'status_enso': status_enso,
            'quinzena_safra': idx_num.add(1).add(offset_quinzena),
            'mes': d1.get('month'),
            'data_inicio': d1.format('YYYY-MM-dd'),
            'data_fim': d2.format('YYYY-MM-dd')
        }))

    fc = ee.FeatureCollection(lista_indices.map(processar_quinzena)).flatten()
    features = fc.getInfo()['features']
    return pd.DataFrame([f['properties'] for f in features])

lista_dfs = []
print(f"Iniciando ingestão por Ano-Safra ({ANO_SAFRA_INICIO}/{ANO_SAFRA_INICIO+1} a {ANO_SAFRA_FIM}/{ANO_SAFRA_FIM+1})...\n")

for ano_safra in range(ANO_SAFRA_INICIO, ANO_SAFRA_FIM + 1):
    t0 = time.time()
    safra_nome = f"{ano_safra}/{ano_safra+1}"
    print(f"-> Safra {safra_nome} ({CLASSIFICACAO_ENSO.get(safra_nome)}): ", end="")

    for sem in [1, 2]:
        for tentativa in range(3):
            try:
                df_sem = extrair_semestre_safra(ano_safra, sem)
                lista_dfs.append(df_sem)
                print(f"[Sem {sem} OK] ", end="", flush=True)
                time.sleep(1.5)
                break
            except Exception as e:
                if tentativa < 2:
                    time.sleep(5)
                else:
                    print(f"\n   [Falha Sem {sem} de {safra_nome}]: {e}")
    print(f"({round(time.time() - t0, 1)}s)")

# ==========================================
# 4. CONSOLIDAÇÃO E MATEMÁTICA DATAFRAME
# ==========================================
df_safras = pd.concat(lista_dfs, ignore_index=True)

colunas_schema = [
    'talhao_id', 'safra', 'status_enso', 'mes', 'quinzena_safra', 'data_inicio', 'data_fim',
    'NDVI', 'EVI', 'Chuva_mm', 'Dias_Secos', 'Temp_Dia_C', 'Temp_Noite_C'
]
df_safras = df_safras[[c for c in colunas_schema if c in df_safras.columns]]
df_safras = df_safras.sort_values(by=['talhao_id', 'safra', 'quinzena_safra']).reset_index(drop=True)

df_safras['NDVI_interpolado'] = df_safras.groupby(['talhao_id', 'safra'])['NDVI'].transform(
    lambda x: x.interpolate(method='linear', limit_direction='both')
)
df_safras['EVI_interpolado'] = df_safras.groupby(['talhao_id', 'safra'])['EVI'].transform(
    lambda x: x.interpolate(method='linear', limit_direction='both')
)

normais = df_safras.groupby(['talhao_id', 'quinzena_safra']).agg(
    EVI_Normal_8safras=('EVI_interpolado', 'mean'),
    Chuva_Normal_8safras_mm=('Chuva_mm', 'mean'),
    Temp_Dia_Normal_8safras_C=('Temp_Dia_C', 'mean')
).reset_index()

df_final = df_safras.merge(normais, on=['talhao_id', 'quinzena_safra'], how='left')

df_final['Anomalia_Chuva_mm'] = df_final['Chuva_mm'] - df_final['Chuva_Normal_8safras_mm']
df_final['Anomalia_Temp_Dia_C'] = df_final['Temp_Dia_C'] - df_final['Temp_Dia_Normal_8safras_C']
df_final['Desvio_EVI_Pct'] = ((df_final['EVI_interpolado'] - df_final['EVI_Normal_8safras']) / df_final['EVI_Normal_8safras']) * 100

df_final['Chuva_Acumulada_Safra_mm'] = df_final.groupby(['talhao_id', 'safra'])['Chuva_mm'].cumsum()
df_final['Saldo_Hidrico_Safra_mm'] = df_final.groupby(['talhao_id', 'safra'])['Anomalia_Chuva_mm'].cumsum()

df_final = df_final.round(3)

# ==========================================
# 5. EXPORTAÇÃO PARA O BIGQUERY
# ==========================================
print(f"Exportando {len(df_final)} registros para o BigQuery na tabela {BQ_TABLE}...")

# O pandas-gbq converte automaticamente o DataFrame para Parquet por baixo dos panos na hora do upload,
# garantindo os tipos de dados e economizando banda.
df_final.to_gbq(
    destination_table=BQ_TABLE,
    project_id=PROJECT_ID,
    if_exists='append', # Use 'append' caso este script rode recorrentemente para adicionar novas quinzenas
)

print("Job finalizado com sucesso!")