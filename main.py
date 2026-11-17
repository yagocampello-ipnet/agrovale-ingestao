import ee
import pandas as pd
import time
import google.auth

PROJECT_ID = 'agrovale-gcp' 
BQ_TABLE_15D = 'agrovale_dataset.bronze_15d' 
BQ_TABLE_DIARIA = 'agrovale_dataset.bronze_diaria' 

print("Autenticando no Google Earth Engine...")
credentials, project = google.auth.default(
    scopes=[
        "https://www.googleapis.com/auth/earthengine",
        "https://www.googleapis.com/auth/cloud-platform"
    ]
)
ee.Initialize(credentials=credentials, project=PROJECT_ID)

geom_talhao = ee.Geometry.Polygon([[
    [-50.95605046159801,-16.902247766763082],
    [-50.95407635576305,-16.904341891372173],
    [-50.95308930284557,-16.905861143686607],
    [-50.95150143510875,-16.90610750777308],
    [-50.94935566789684,-16.907092960899252],
    [-50.94879776842174,-16.90664129552258],
    [-50.94716698534069,-16.903602791224856],
    [-50.94678074724254,-16.90228882825348],
    [-50.951458519764515,-16.894815489737738],
    [-50.95901162035045,-16.90003043300327],
    [-50.95605046159801,-16.902247766763082]

]])

perimetro_fazenda = geom_talhao

mapbiomas = ee.Image('projects/mapbiomas-public/assets/brazil/lulc/collection9/mapbiomas_collection90_integration_v1')
uso_solo = mapbiomas.select('classification_2023')
mascara_lavoura = uso_solo.eq(39).Or(uso_solo.eq(41)).clip(geom_talhao)

area_total_ha = geom_talhao.area(1).divide(10000).round()
area_util_img = ee.Image.pixelArea().divide(10000).updateMask(mascara_lavoura)
area_util_ha = ee.Number(area_util_img.reduceRegion(
    reducer=ee.Reducer.sum(),
    geometry=geom_talhao,
    scale=10,
    maxPixels=1e8
).get('area')).round()

talhoes_milho = ee.FeatureCollection([
    ee.Feature(geom_talhao, {
        'talhao_id': 'Talhao_Milho_01',
        'uso_solo_mapbiomas': 'Lavoura Temporaria (Soja/Milho)',
        'area_total_ha': area_total_ha,
        'area_ha': area_util_ha
    })
])

s2_ref = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
          .filterBounds(geom_talhao)
          .filterDate('2025-03-01', '2025-06-30')
          .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))
          .median()
          .divide(10000))

evi_ref = s2_ref.expression(
    '2.5 * ((NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1))', {
        'NIR': s2_ref.select('B8'),
        'RED': s2_ref.select('B4'),
        'BLUE': s2_ref.select('B2')
    }).rename('EVI').updateMask(mascara_lavoura)

# --- CONFIGURAÇÃO TEMPORAL COMPATÍVEL COM O CICLO DO MILHO (FEV A SET) ---
ANO_SAFRA_INICIO = 2018  # Inicia na Safra 2018/2019
ANO_SAFRA_FIM = 2025     # Termina na Safra 2025/2026
PASSO_DIAS = 15          # Janela quinzenal (16 quinzenas entre 01/Fev e 28/Set)

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
    return ee.Image.cat([ee.Image.constant(0).selfMask().rename(n) for n in nomes_bandas])

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

DATA_GLOBAL_INI = f'{ANO_SAFRA_INICIO}-01-01'
DATA_GLOBAL_FIM = f'{ANO_SAFRA_FIM + 1}-10-01'

col_s2 = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
          .filterBounds(perimetro_fazenda)
          .filterDate(DATA_GLOBAL_INI, DATA_GLOBAL_FIM)
          .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 70))
          .map(prep_s2))

col_chirps = (ee.ImageCollection('UCSB-CHG/CHIRPS/DAILY')
              .filterBounds(perimetro_fazenda)
              .filterDate(DATA_GLOBAL_INI, DATA_GLOBAL_FIM))

col_modis = (ee.ImageCollection('MODIS/061/MOD11A2')
             .filterBounds(perimetro_fazenda)
             .filterDate(DATA_GLOBAL_INI, DATA_GLOBAL_FIM)
             .map(prep_modis))

def extrair_ciclo_milho_safra(ano_inicio_safra):
    """Extrai 16 quinzenas (01/Fev a 28/Set do ano da safrinha) para os 3 talhões."""
    ano_milho = ano_inicio_safra + 1
    safra_label = f"{ano_inicio_safra}/{ano_milho}"
    status_enso = CLASSIFICACAO_ENSO.get(safra_label, 'Nao Classificado')

    # Início exato do ciclo do milho safrinha em Goiás: 1º de Fevereiro
    start = ee.Date.fromYMD(ano_milho, 2, 1)
    # 16 quinzenas * 15 dias = 240 dias (cobre até final de setembro / colheita completa)
    lista_indices = ee.List.sequence(0, 15)

    def processar_quinzena(idx):
        idx_num = ee.Number(idx)
        d1 = start.advance(idx_num.multiply(PASSO_DIAS), 'day')
        d2 = d1.advance(PASSO_DIAS, 'day')
        mes = d1.get('month')

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

        def adicionar_metadados_milho(f):
            estadio = ee.Algorithms.If(
                mes.eq(2), '1. Plantio / Emergencia (VE-V3)',
                ee.Algorithms.If(
                    mes.eq(3), '2. Vegetativo (V4-V12)',
                    ee.Algorithms.If(
                        mes.eq(4), '3. Pendoamento / Espigamento (VT-R1)',
                        ee.Algorithms.If(
                            mes.eq(5).Or(mes.eq(6)), '4. Enchimento de Graos (R2-R5)',
                            '5. Maturacao / Colheita (R6)'
                        )
                    )
                )
            )
            return f.set({
                'safra': safra_label,
                'ano_colheita': ano_milho,
                'status_enso': status_enso,
                'quinzena_milho': idx_num.add(1), # 1 a 16 dentro do ciclo do milho
                'estadio_fenologico': estadio,
                'mes': mes,
                'data_inicio': d1.format('YYYY-MM-dd'),
                'data_fim': d2.format('YYYY-MM-dd')
            })

        return stats.map(adicionar_metadados_milho)

    fc = ee.FeatureCollection(lista_indices.map(processar_quinzena)).flatten()
    features = fc.getInfo()['features']
    return pd.DataFrame([f['properties'] for f in features])

lista_dfs_milho = []
print("1. Iniciando extração temporal do Talhao_Milho_01 (Ciclo Fev-Set)...\n")

for ano_safra in range(ANO_SAFRA_INICIO, ANO_SAFRA_FIM + 1):
    t0 = time.time()
    safra_nome = f"{ano_safra}/{ano_safra+1}"
    print(f"-> Safra {safra_nome} ({CLASSIFICACAO_ENSO.get(safra_nome)}): ", end="")

    for tentativa in range(3):
        try:
            df_safra = extrair_ciclo_milho_safra(ano_safra)
            lista_dfs_milho.append(df_safra)
            print(f"OK ({len(df_safra)} registros em {round(time.time() - t0, 1)}s)")
            time.sleep(1.5)
            break
        except Exception as e:
            if tentativa < 2:
                time.sleep(5)
            else:
                print(f"Erro: {e}")

df_milho = pd.concat(lista_dfs_milho, ignore_index=True)

# Incluída a coluna 'area_total_ha' criada na Célula 2
colunas_milho = [
    'talhao_id', 'area_total_ha', 'area_ha', 'uso_solo_mapbiomas',
    'safra', 'ano_colheita', 'status_enso',
    'quinzena_milho', 'estadio_fenologico', 'mes', 'data_inicio', 'data_fim',
    'NDVI', 'EVI', 'Chuva_mm', 'Dias_Secos', 'Temp_Dia_C', 'Temp_Noite_C'
]
df_milho = df_milho[[c for c in colunas_milho if c in df_milho.columns]]
df_milho = df_milho.sort_values(by=['talhao_id', 'safra', 'quinzena_milho']).reset_index(drop=True)

# Interpolação e cálculo de normais/anomalias por talhão e quinzena do milho
df_milho['NDVI_interpolado'] = df_milho.groupby(['talhao_id', 'safra'])['NDVI'].transform(
    lambda x: x.interpolate(method='linear', limit_direction='both')
)
df_milho['EVI_interpolado'] = df_milho.groupby(['talhao_id', 'safra'])['EVI'].transform(
    lambda x: x.interpolate(method='linear', limit_direction='both')
)

normais = df_milho.groupby(['talhao_id', 'quinzena_milho']).agg(
    EVI_Normal_Historico=('EVI_interpolado', 'mean'),
    Chuva_Normal_Historico_mm=('Chuva_mm', 'mean'),
    Temp_Dia_Normal_Historico_C=('Temp_Dia_C', 'mean')
).reset_index()

df_milho_final = df_milho.merge(normais, on=['talhao_id', 'quinzena_milho'], how='left')
df_milho_final['Anomalia_Chuva_mm'] = df_milho_final['Chuva_mm'] - df_milho_final['Chuva_Normal_Historico_mm']
df_milho_final['Anomalia_Temp_Dia_C'] = df_milho_final['Temp_Dia_C'] - df_milho_final['Temp_Dia_Normal_Historico_C']
df_milho_final['Desvio_EVI_Pct'] = ((df_milho_final['EVI_interpolado'] - df_milho_final['EVI_Normal_Historico']) / df_milho_final['EVI_Normal_Historico']) * 100
df_milho_final['Chuva_Acumulada_Ciclo_mm'] = df_milho_final.groupby(['talhao_id', 'safra'])['Chuva_mm'].cumsum()

df_milho_final = df_milho_final.round(3)

print(f"\nTabela 1 (Talhao_Milho_01 Quinzenal) salva com sucesso! ({len(df_milho_final)} linhas)")


print(f"Exportando {len(df_milho_final)} registros para o BigQuery na tabela {BQ_TABLE}...")

# O pandas-gbq converte automaticamente o DataFrame para Parquet por baixo dos panos na hora do upload,
# garantindo os tipos de dados e economizando banda.
df_milho_final.to_gbq(
    destination_table=BQ_TABLE_15D,
    project_id=PROJECT_ID,
    if_exists='append', # Use 'append' caso este script rode recorrentemente para adicionar novas quinzenas
)



print("2. Iniciando extração da Tabela Diária de Chuva (CHIRPS Daily) para o Talhao_Milho_01...\n")

def extrair_chuva_diaria_ano(ano):
    """Extrai todos os dias de um ano civil do CHIRPS Daily para o talhão específico."""
    d_ini = f'{ano}-01-01'
    d_fim = f'{ano + 1}-01-01'

    chirps_ano = (ee.ImageCollection('UCSB-CHG/CHIRPS/DAILY')
                  .filterBounds(geom_talhao)
                  .filterDate(d_ini, d_fim))

    def reduzir_dia(img):
        stats = img.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=geom_talhao,
            scale=5000,
            maxPixels=1e8
        )
        return ee.Feature(None, {
            'talhao_id': 'Talhao_Milho_01', # Chave de relacionamento com a Tabela 1
            'data': ee.Date(img.get('system:time_start')).format('YYYY-MM-dd'),
            'ano': ano,
            'mes': ee.Date(img.get('system:time_start')).get('month'),
            'dia_ano': ee.Date(img.get('system:time_start')).getRelative('day', 'year').add(1),
            'chuva_diaria_mm': stats.get('precipitation')
        })

    fc_dias = chirps_ano.map(reduzir_dia)
    features = fc_dias.getInfo()['features']
    return pd.DataFrame([f['properties'] for f in features])

lista_dfs_chuva = []

for ano in range(2018, 2027):
    t0 = time.time()
    print(f"-> Extraindo chuva diária de {ano}: ", end="")
    try:
        df_ano_chuva = extrair_chuva_diaria_ano(ano)
        lista_dfs_chuva.append(df_ano_chuva)
        print(f"OK ({len(df_ano_chuva)} dias em {round(time.time() - t0, 1)}s)")
        time.sleep(1.0)
    except Exception as e:
        print(f"Erro em {ano}: {e}")

df_chuva_diaria = pd.concat(lista_dfs_chuva, ignore_index=True)
df_chuva_diaria['data'] = pd.to_datetime(df_chuva_diaria['data'])
df_chuva_diaria = df_chuva_diaria.sort_values(by=['talhao_id', 'data']).reset_index(drop=True)

# --- ENGENHARIA DE FEATURES PLUVIOMÉTRICAS DIÁRIAS ---
df_chuva_diaria['chuva_diaria_mm'] = df_chuva_diaria['chuva_diaria_mm'].fillna(0).round(2)

# Flag de dia seco (< 2 mm) e contagem de dias secos consecutivos
df_chuva_diaria['eh_dia_seco'] = (df_chuva_diaria['chuva_diaria_mm'] < 2.0).astype(int)
grupos_chuva = (df_chuva_diaria['eh_dia_seco'] == 0).cumsum()
df_chuva_diaria['dias_secos_consecutivos'] = df_chuva_diaria.groupby(grupos_chuva)['eh_dia_seco'].cumsum()

# Médias móveis de chuva acumulada (7, 15 e 30 dias)
df_chuva_diaria['chuva_acum_7d_mm'] = df_chuva_diaria['chuva_diaria_mm'].rolling(window=7, min_periods=1).sum().round(2)
df_chuva_diaria['chuva_acum_15d_mm'] = df_chuva_diaria['chuva_diaria_mm'].rolling(window=15, min_periods=1).sum().round(2)
df_chuva_diaria['chuva_acum_30d_mm'] = df_chuva_diaria['chuva_diaria_mm'].rolling(window=30, min_periods=1).sum().round(2)

# Ordenar colunas colocando talhao_id primeiro
colunas_chuva = [
    'talhao_id', 'data', 'ano', 'mes', 'dia_ano',
    'chuva_diaria_mm', 'eh_dia_seco', 'dias_secos_consecutivos',
    'chuva_acum_7d_mm', 'chuva_acum_15d_mm', 'chuva_acum_30d_mm'
]
df_chuva_diaria = df_chuva_diaria[colunas_chuva]
df_chuva_diaria['data'] = df_chuva_diaria['data'].dt.strftime('%Y-%m-%d')

print(f"\nTabela 2 (Chuva Diária Talhao_Milho_01) salva com sucesso! ({len(df_chuva_diaria)} dias registrados)")

df_chuva_diaria.to_gbq(
    destination_table=BQ_TABLE_DIARIA,
    project_id=PROJECT_ID,
    if_exists='append', # Use 'append' caso este script rode recorrentemente para adicionar novas quinzenas
)