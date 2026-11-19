/**
 * AgroVale — Sincronização Automatizada BigQuery (Camada Gold) -> Google Sheets (Canvas)
 * Responsável: TSS / Colaboração
 * Frequência: Acionador baseado no tempo (dias 1 e 16 de cada mês)
 */

const ID_PLANILHA_RAMON = "18C7lDtwR19gR0aYDp5R05LzpA_T4XZwQE9mWNuPNpyI";
const PROJETO_GCP = "agrovale-gcp";
const REGIAO_BQ = "us-west1";

function atualizarDadosGoldAutomatico() {
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
