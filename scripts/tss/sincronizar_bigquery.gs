/**
- AgroVale — Sincronização Automatizada BigQuery (Camada Gold) -> Google Sheets (Canvas)
- Responsável: TSS / Colaboração
- Frequência: Polling inteligente por metadados (a cada 5 minutos)
 */

const ID_PLANILHA_RAMON = "18C7lDtwR19gR0aYDp5R05LzpA_T4XZwQE9mWNuPNpyI";
const PROJETO_GCP = "agrovale-gcp";
const REGIAO_BQ = "us-west1";

function atualizarDadosGoldAutomatico_() {
  const tabelasParaExtrair = [
    // --- ESSENCIAIS ---
    { aba: "fato_quinzenal", sql: "SELECT * FROM `agrovale-gcp.gold.fato_quinzenal` ORDER BY talhao_id ASC, data_inicio DESC" },
    { aba: "calendario", sql: "SELECT * FROM `agrovale-gcp.gold.calendario`" },
    { aba: "calendario_veg_marcos", sql: "SELECT * FROM `agrovale-gcp.gold.calendario_vegetacao_marcos`" },
    { aba: "risco_veranico", sql: "SELECT * FROM `agrovale-gcp.gold.risco_veranico`" },
    { aba: "fato_mensal", sql: "SELECT * FROM `agrovale-gcp.gold.fato_mensal`" },
    { aba: "chuva_mensal", sql: "SELECT * FROM `agrovale-gcp.gold.chuva_mensal`" },
    { aba: "janela_plantio", sql: "SELECT * FROM `agrovale-gcp.gold.janela_plantio`" },

    // --- COMPLEMENTARES ---
    { aba: "comparativo_censo", sql: "SELECT * FROM `agrovale-gcp.gold.comparativo_censo`" },
    { aba: "janela_plantio_resumo", sql: "SELECT * FROM `agrovale-gcp.gold.janela_plantio_resumo`" },
    { aba: "resposta_seca", sql: "SELECT * FROM `agrovale-gcp.gold.resposta_seca`" },
    { aba: "talhao", sql: "SELECT * FROM `agrovale-gcp.gold.talhao`" },
    { aba: "tendencia", sql: "SELECT * FROM `agrovale-gcp.gold.tendencia`" }
  ];

  const ss = SpreadsheetApp.openById(ID_PLANILHA_RAMON);

  for (const item of tabelasParaExtrair) {
    try {
      const request = {
        query: item.sql,
        useLegacySql: false,
        location: REGIAO_BQ
      };

      const queryResults = BigQuery.Jobs.query(request, PROJETO_GCP);

      if (!queryResults.rows || queryResults.rows.length === 0) {
        Logger.log(`Aviso: Tabela ${item.aba} retornou vazia.`);
        continue;
      }

      const headers = queryResults.schema.fields.map(f => f.name);
      const rows = queryResults.rows.map(row => {
        return row.f.map((cell, i) => {
          if (cell.v === null) return "";
          const tipo = queryResults.schema.fields[i].type;
          if (tipo === "FLOAT" || tipo === "INTEGER" || tipo === "NUMERIC") {
            return Number(cell.v);
          }
          return cell.v;
        });
      });

      let sheet = ss.getSheetByName(item.aba);
      if (!sheet) {
        sheet = ss.insertSheet(item.aba);
      }

      const dadosCompletos = [headers, ...rows];
      sheet.clearContents();
      sheet.getRange(1, 1, dadosCompletos.length, headers.length).setValues(dadosCompletos);

      Logger.log(`Sucesso: ${item.aba} (${rows.length} linhas atualizadas).`);
    } catch (erro) {
      Logger.log(`Aviso/Erro em ${item.aba}: ${erro.message}`);
    }
  }
}

function verificarEAtualizarGold() {
  const datasetId = "gold";
  const tableId = "fato_quinzenal"; // Tabela principal de referência da camada Gold

  try {
    // Consulta apenas os metadados da tabela (gratuito e instantâneo)
    const tabela = BigQuery.Tables.get(PROJETO_GCP, datasetId, tableId);
    const ultimaModificacaoBQ = String(tabela.lastModifiedTime);

    const props = PropertiesService.getScriptProperties();
    const ultimaExecucaoSalva = props.getProperty("ULTIMA_MODIFICACAO_GOLD");

    if (ultimaModificacaoBQ !== ultimaExecucaoSalva) {
      Logger.log(`Alteração detectada no BigQuery (${ultimaModificacaoBQ}). Atualizando planilha...`);
      atualizarDadosGoldAutomatico_();
      props.setProperty("ULTIMA_MODIFICACAO_GOLD", ultimaModificacaoBQ);
    } else {
      Logger.log("Nenhuma alteração na camada Gold desde a última checagem. Execução pulada.");
    }
  } catch (erro) {
    Logger.log(`Erro ao verificar metadados do BigQuery: ${erro.message}`);
  }
}
