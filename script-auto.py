import ee
import pandas as pd
import time
import datetime
import os
import google.auth
import gspread
from google.cloud import bigquery
from google.cloud import dataform_v1beta1 

PROJECT_ID = 'agrovale-gcp' 
BQ_TABLE_15D = 'agrovale_dataset.bronze_15d' 
BQ_TABLE_DIARIA = 'agrovale_dataset.bronze_diaria' 
SPREADSHEET_ID = '1JbyW-wrMfkSi_c6i8jK-gfvKp1h8NyrxvgnHrzGQUEA'

print("Autenticando no Google Earth Engine, BigQuery e Sheets...")
credentials, project = google.auth.default(
    scopes=[
        "https://www.googleapis.com/auth/earthengine",
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/spreadsheets.readonly"
    ]
)
ee.Initialize(credentials=credentials, project=PROJECT_ID)
bq_client = bigquery.Client(credentials=credentials, project=PROJECT_ID)

hoje = datetime.date.today()
ano_atual = hoje.year
data_ingestao_atual = pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')

# Tenta capturar a variável injetada pelo webhook do Apps Script
talhao_novo = os.environ.get("TALHAO_ID_NOVO")

# ==============================================================================
# FUNÇÃO AUXILIAR: BUSCAR ÚLTIMA DATA NO BIGQUERY
# ==============================================================================
def obter_valor_maximo_bq(tabela, coluna):
    try:
        query = f"SELECT MAX({coluna}) as max_val FROM `{PROJECT_ID}.{tabela}`"
        df_bq = bq_client.query(query).to_dataframe()
        if not df_bq.empty and pd.notna(df_bq.iloc[0]['max_val']):
            return df_bq.iloc[0]['max_val']
    except Exception:
        pass
    return None

# ==============================================================================
# 0. LER TALHÕES DO GOOGLE SHEETS E CONSTRUIR GEOMETRIAS
# ==============================================================================
print("Lendo dados do Google Sheets...")
gc = gspread.authorize(credentials)
planilha = gc.open_by_key(SPREADSHEET_ID)
aba = planilha.sheet1
dados_forms = aba.get_all_records()
df_sheets = pd.DataFrame(dados_forms)

mapbiomas = ee.Image('projects/mapbiomas-public/assets/brazil/lulc/collection9/mapbiomas_collection90_integration_v1')
uso_solo = mapbiomas.select('classification_2023')

lista_features_talhoes = []

for index, row in df_sheets.iterrows():
    talhao_id = str(row.get('Nome de Identificação do Talhão', '')).strip()
    if not talhao_id:
        continue
        
    coords = []
    # Varre as 11 possíveis colunas de coordenadas no Forms
    for i in range(1, 12):
        lat_col = f"Latitude {i}"
        lon_col = f"Longitude {i}"
        
        if lat_col in df_sheets.columns and lon_col in df_sheets.columns:
            lat = row[lat_col]
            lon = row[lon_col]
            
            # Se preencheu essa coordenada, adiciona na lista (GEE usa [Longitude, Latitude])
            if pd.notna(lat) and pd.notna(lon) and str(lat).strip() != '' and str(lon).strip() != '':
                coords.append([float(lon), float(lat)])
                
    if len(coords) >= 3: # Um polígono precisa de pelo menos 3 pontos
        # Garante que o polígono está fechado (último ponto igual ao primeiro)
        if coords[0] != coords[-1]:
            coords.append(coords[0])
            
        geom = ee.Geometry.Polygon([coords])
        mascara_lavoura = uso_solo.eq(39).Or(uso_solo.eq(41)).clip(geom)
        
        area_total_ha = geom.area(1).divide(10000).round()
        area_util_img = ee.Image.pixelArea().divide(10000).updateMask(mascara_lavoura)
        area_util_ha = ee.Number(area_util_img.reduceRegion(
            reducer=ee.Reducer.sum(), geometry=geom, scale=10, maxPixels=1e8
        ).get('area')).round()
        
        feature = ee.Feature(geom, {
            'talhao_id': talhao_id,
            'uso_solo_mapbiomas': 'Lavoura Temporaria (Soja/Milho)',
            'area_total_ha': area_total_ha,
            'area_ha': area_util_ha
        })
        lista_features_talhoes.append(feature)

if talhao_novo:
    print(f"Execução instantânea (Webhook) detectada para o talhão: {talhao_novo}")
    # Filtra a lista para rodar APENAS o novo talhão
    lista_features_talhoes = [f for f in lista_features_talhoes if f.get('talhao_id').getInfo() == talhao_novo]
else:
    print("Execução de rotina detectada. Processando todos os talhões da planilha.")

if not lista_features_talhoes:
    print("Nenhum talhão válido para processar. Encerrando.")
    exit(0)

talhoes_milho = ee.FeatureCollection(lista_features_talhoes)
perimetro_fazenda = talhoes_milho.geometry()

# ==============================================================================
# FUNÇÕES GEE
# ==============================================================================
def imagem_vazia(nomes_bandas):
    return ee.Image.cat([ee.Image.constant(0).selfMask().rename(n) for n in nomes_bandas])

def prep_s2(img):
    scl = img.select('SCL')
    mascara_lavoura_col = uso_solo.eq(39).Or(uso_solo.eq(41)).clip(perimetro_fazenda)
    mask_final = scl.eq(4).Or(scl.eq(5)).Or(scl.eq(6)).And(mascara_lavoura_col)
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

# ==============================================================================
# 1. TABELA 15D (CICLO MILHO SAFRA)
# ==============================================================================
print("\n1. Iniciando extração da Tabela Quinzenal (Ciclo do Milho)...\n")

if talhao_novo:
    ANO_SAFRA_INICIO = 2018
else:
    max_ano_colheita = obter_valor_maximo_bq(BQ_TABLE_15D, 'ano_colheita')
    if max_ano_colheita is None:
        ANO_SAFRA_INICIO = 2018
    else:
        ANO_SAFRA_INICIO = int(max_ano_colheita) - 1 

ANO_SAFRA_FIM = ano_atual
PASSO_DIAS = 15

CLASSIFICACAO_CENSO = {
    '2018/2019': 'El Nino (Fraco)', '2019/2020': 'Neutro (Normal)',
    '2020/2021': 'La Nina', '2021/2022': 'La Nina',
    '2022/2023': 'Neutro / Transicao', '2023/2024': 'El Nino (Forte)',
    '2024/2025': 'Neutro / La Nina Fraca', '2025/2026': 'Neutro (Normal)',
    '2026/2027': 'Nao Classificado'
}

DATA_GLOBAL_INI = f'{ANO_SAFRA_INICIO}-01-01'
DATA_GLOBAL_FIM = f'{ANO_SAFRA_FIM + 1}-10-01'

col_s2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED').filterBounds(perimetro_fazenda).filterDate(DATA_GLOBAL_INI, DATA_GLOBAL_FIM).filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 70)).map(prep_s2)
col_chirps = ee.ImageCollection('UCSB-CHG/CHIRPS/DAILY').filterBounds(perimetro_fazenda).filterDate(DATA_GLOBAL_INI, DATA_GLOBAL_FIM)
col_modis = ee.ImageCollection('MODIS/061/MOD11A2').filterBounds(perimetro_fazenda).filterDate(DATA_GLOBAL_INI, DATA_GLOBAL_FIM).map(prep_modis)

def extrair_ciclo_milho_safra(ano_inicio_safra):
    ano_milho = ano_inicio_safra + 1
    safra_label = f"{ano_inicio_safra}/{ano_milho}"
    status_censo = CLASSIFICACAO_CENSO.get(safra_label, 'Nao Classificado')
    start = ee.Date.fromYMD(ano_milho, 2, 1)
    lista_indices = ee.List.sequence(0, 15)

    def processar_quinzena(idx):
        idx_num = ee.Number(idx)
        d1 = start.advance(idx_num.multiply(PASSO_DIAS), 'day')
        d2 = d1.advance(PASSO_DIAS, 'day')
        mes = d1.get('month')

        s2_sub = col_s2.filterDate(d1, d2)
        s2_img = ee.Image(ee.Algorithms.If(s2_sub.size().gt(0), s2_sub.median(), imagem_vazia(['NDVI', 'EVI'])))

        chirps_sub = col_chirps.filterDate(d1, d2)
        chuva_img = chirps_sub.sum().rename('Chuva_mm')
        dias_secos_img = chirps_sub.map(lambda i: i.lt(2)).sum().rename('Dias_Secos')

        modis_sub = col_modis.filterDate(d1, d2)
        temp_img = ee.Image(ee.Algorithms.If(modis_sub.size().gt(0), modis_sub.mean(), imagem_vazia(['Temp_Dia_C', 'Temp_Noite_C'])))

        stack = ee.Image.cat([s2_img, chuva_img, dias_secos_img, temp_img])
        stats = stack.reduceRegions(collection=talhoes_milho, reducer=ee.Reducer.mean(), scale=10, tileScale=4)

        def adicionar_metadados_milho(f):
            estadio = ee.Algorithms.If(mes.eq(2), '1. Plantio / Emergencia (VE-V3)', ee.Algorithms.If(mes.eq(3), '2. Vegetativo (V4-V12)', ee.Algorithms.If(mes.eq(4), '3. Pendoamento / Espigamento (VT-R1)', ee.Algorithms.If(mes.eq(5).Or(mes.eq(6)), '4. Enchimento de Graos (R2-R5)', '5. Maturacao / Colheita (R6)'))))
            return f.set({
                'safra': safra_label, 'ano_colheita': ano_milho, 'status_censo': status_censo,
                'quinzena_milho': idx_num.add(1), 'estadio_fenologico': estadio,
                'mes': mes, 'data_inicio': d1.format('YYYY-MM-dd'), 'data_fim': d2.format('YYYY-MM-dd')
            })
        return stats.map(adicionar_metadados_milho)

    fc = ee.FeatureCollection(lista_indices.map(processar_quinzena)).flatten()
    features = fc.getInfo()['features']
    return pd.DataFrame([f['properties'] for f in features])

lista_dfs_milho = []
for ano_safra in range(ANO_SAFRA_INICIO, ANO_SAFRA_FIM + 1):
    t0 = time.time()
    safra_nome = f"{ano_safra}/{ano_safra+1}"
    print(f"-> Extraindo Safra {safra_nome}: ", end="")
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
df_milho['data_inicio'] = pd.to_datetime(df_milho['data_inicio'])
df_milho = df_milho[df_milho['data_inicio'].dt.date <= hoje]
df_milho['data_inicio'] = df_milho['data_inicio'].dt.strftime('%Y-%m-%d')

df_milho['data_ingestao'] = data_ingestao_atual

colunas_milho = [
    'talhao_id', 'area_total_ha', 'area_ha', 'uso_solo_mapbiomas',
    'safra', 'ano_colheita', 'status_censo', 'quinzena_milho', 'estadio_fenologico', 
    'mes', 'data_inicio', 'data_fim', 'NDVI', 'EVI', 'Chuva_mm', 'Dias_Secos', 'Temp_Dia_C', 'Temp_Noite_C', 'data_ingestao'
]
df_milho = df_milho[[c for c in colunas_milho if c in df_milho.columns]]
df_milho = df_milho.sort_values(by=['talhao_id', 'safra', 'quinzena_milho']).reset_index(drop=True)

if not talhao_novo and max_ano_colheita is not None:
    print(f"Limpando safras >= {ANO_SAFRA_INICIO + 1} do BigQuery para reposição incremental...")
    bq_client.query(f"DELETE FROM `{PROJECT_ID}.{BQ_TABLE_15D}` WHERE ano_colheita >= {ANO_SAFRA_INICIO + 1}").result()

print(f"Exportando {len(df_milho)} registros quinzenais para o BigQuery...")
df_milho.to_gbq(destination_table=BQ_TABLE_15D, project_id=PROJECT_ID, if_exists='append')

# ==============================================================================
# 2. TABELA DIÁRIA - LÓGICA INCREMENTAL (WARM-UP WINDOW)
# ==============================================================================
print("\n2. Iniciando extração da Tabela Diária de Chuva...\n")

if talhao_novo:
    data_inicio_extracao = '2018-01-01'
    df_historico_bq = pd.DataFrame()
else:
    query_max = f"SELECT MAX(data) as max_data FROM `{PROJECT_ID}.{BQ_TABLE_DIARIA}`"
    df_max = bq_client.query(query_max).to_dataframe()

    if df_max.empty or pd.isna(df_max.iloc[0]['max_data']):
        data_inicio_extracao = '2018-01-01'
        df_historico_bq = pd.DataFrame()
    else:
        ultima_data_bq = pd.to_datetime(df_max.iloc[0]['max_data'])
        data_inicio_extracao = (ultima_data_bq + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
        
        print(f"Último dado no BQ: {ultima_data_bq.strftime('%Y-%m-%d')}. Puxando 40 dias de BQ para apoio de média...")
        query_hist = f"""
            SELECT talhao_id, data, chuva_diaria_mm, eh_dia_seco, dias_secos_consecutivos 
            FROM `{PROJECT_ID}.{BQ_TABLE_DIARIA}` 
            WHERE data > DATE_SUB(DATE('{ultima_data_bq.strftime('%Y-%m-%d')}'), INTERVAL 40 DAY)
            ORDER BY data ASC
        """
        df_historico_bq = bq_client.query(query_hist).to_dataframe()
        df_historico_bq['data'] = pd.to_datetime(df_historico_bq['data'])

hoje_str = hoje.strftime('%Y-%m-%d')
data_fim_extracao = (hoje + pd.Timedelta(days=1)).strftime('%Y-%m-%d')

if not talhao_novo and data_inicio_extracao > hoje_str:
    print("Os dados diários já estão atualizados.")
else:
    print(f"Extraindo GEE de {data_inicio_extracao} até {hoje_str}...")
    chirps_novos = ee.ImageCollection('UCSB-CHG/CHIRPS/DAILY').filterBounds(perimetro_fazenda).filterDate(data_inicio_extracao, data_fim_extracao)
    
    def extrair_chuva_talhao(feature_talhao):
        id_t = feature_talhao.get('talhao_id')
        geom_t = feature_talhao.geometry()
        
        def reduce_local(img):
            stat = img.reduceRegion(reducer=ee.Reducer.mean(), geometry=geom_t, scale=5000, maxPixels=1e8)
            return ee.Feature(None, {
                'talhao_id': id_t,
                'data': ee.Date(img.get('system:time_start')).format('YYYY-MM-dd'),
                'chuva_diaria_mm': stat.get('precipitation')
            })
        return chirps_novos.map(reduce_local)

    fc_chuva_nested = talhoes_milho.map(extrair_chuva_talhao).flatten()
    features = fc_chuva_nested.getInfo()['features']
    df_novos = pd.DataFrame([f['properties'] for f in features])
    
    if not df_novos.empty:
        df_novos['data'] = pd.to_datetime(df_novos['data'])
        df_novos['chuva_diaria_mm'] = df_novos['chuva_diaria_mm'].fillna(0).round(2)
        df_novos['eh_dia_seco'] = (df_novos['chuva_diaria_mm'] < 2.0).astype(int)
        
        df_calc = pd.concat([df_historico_bq, df_novos], ignore_index=True) if not df_historico_bq.empty else df_novos
        df_calc = df_calc.sort_values(by=['talhao_id', 'data']).reset_index(drop=True)
        
        df_calc['dias_secos_consecutivos'] = df_calc.groupby('talhao_id').apply(
            lambda g: g.groupby((g['eh_dia_seco'] == 0).cumsum())['eh_dia_seco'].cumsum()
        ).reset_index(level=0, drop=True)
        
        df_calc['chuva_acum_7d_mm'] = df_calc.groupby('talhao_id')['chuva_diaria_mm'].transform(lambda x: x.rolling(7, min_periods=1).sum().round(2))
        df_calc['chuva_acum_15d_mm'] = df_calc.groupby('talhao_id')['chuva_diaria_mm'].transform(lambda x: x.rolling(15, min_periods=1).sum().round(2))
        df_calc['chuva_acum_30d_mm'] = df_calc.groupby('talhao_id')['chuva_diaria_mm'].transform(lambda x: x.rolling(30, min_periods=1).sum().round(2))
        
        if not df_historico_bq.empty:
            df_final = df_calc[df_calc['data'] > ultima_data_bq].copy()
        else:
            df_final = df_calc.copy()
            
        df_final['ano'] = df_final['data'].dt.year
        df_final['mes'] = df_final['data'].dt.month
        df_final['dia_ano'] = df_final['data'].dt.dayofyear
        df_final['data'] = df_final['data'].dt.strftime('%Y-%m-%d')
        df_final['data_ingestao'] = data_ingestao_atual
        
        colunas_chuva = [
            'talhao_id', 'data', 'ano', 'mes', 'dia_ano',
            'chuva_diaria_mm', 'eh_dia_seco', 'dias_secos_consecutivos',
            'chuva_acum_7d_mm', 'chuva_acum_15d_mm', 'chuva_acum_30d_mm', 'data_ingestao'
        ]
        df_final = df_final[colunas_chuva]
        
        print(f"Exportando {len(df_final)} novos registros diários para o BigQuery...")
        df_final.to_gbq(destination_table=BQ_TABLE_DIARIA, project_id=PROJECT_ID, if_exists='append')

print("\nIngestão finalizada. Acionando pipeline do Dataform...")

# ==============================================================================
# 3. ACIONAR O DATAFORM
# ==============================================================================
try:
    df_client = dataform_v1beta1.DataformClient(credentials=credentials)
    
    # Configure com base no que constava nos seus logs anteriores:
    DATAFORM_REGION = 'us-west1' 
    DATAFORM_REPO = 'Agrovale-dataform'
    
    parent = f"projects/{PROJECT_ID}/locations/{DATAFORM_REGION}/repositories/{DATAFORM_REPO}"
    
    # Passo 1: Compilar o código (usa a branch 'main' por padrão)
    compilation_request = dataform_v1beta1.CreateCompilationResultRequest(
        parent=parent,
        compilation_result=dataform_v1beta1.CompilationResult(
            git_commitish="main" # Altere aqui se a sua branch principal tiver outro nome
        )
    )
    compilation_result = df_client.create_compilation_result(request=compilation_request)
    print(f"Dataform - Código compilado com sucesso.")
    
    # Passo 2: Executar as transformações Silver/Gold
    invocation_request = dataform_v1beta1.CreateWorkflowInvocationRequest(
        parent=parent,
        workflow_invocation=dataform_v1beta1.WorkflowInvocation(
            compilation_result=compilation_result.name
        )
    )
    invocation_result = df_client.create_workflow_invocation(request=invocation_request)
    print(f"Dataform - Execução iniciada com sucesso. ID: {invocation_result.name}")

except Exception as e:
    print(f"Atenção: A ingestão foi concluída, mas houve um erro ao acionar o Dataform: {e}")

print("\nPipeline finalizado com sucesso!")