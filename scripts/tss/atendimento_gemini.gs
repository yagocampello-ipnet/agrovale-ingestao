/**
 * AgroVale — Atendimento N1 Assistido por IA (Forms + BigQuery + Gemini)
 * Responsável: TSS / Colaboração
 * Acionador: On form submit (ao enviar resposta do formulário)
 */

const GEMINI_API_KEY = "SUA_CHAVE_API_GEMINI_AQUI";

function gerarSugestaoGemini(e) {
  // Pausa de 3 segundos para evitar disputa com o outro acionador (criarDropdownNovoTicket)
  Utilities.sleep(3000);

  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName("Form Responses 1") || ss.getSheetByName("Form_Responses") || ss.getSheets()[0];
  const linha = (e && e.range && e.range.getRow() > 1) ? e.range.getRow() : sheet.getLastRow();
  
  if (linha <= 1) return;

  // Garante o cabeçalho da Coluna P (16)
  if (sheet.getRange(1, 16).getValue() === "") {
    sheet.getRange(1, 16).setValue("Sugestao_Resposta_Gemini");
  }

  const cabecalhos = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  const valoresLinha = sheet.getRange(linha, 1, 1, sheet.getLastColumn()).getValues()[0];
  
  const idxTalhao = cabecalhos.findIndex(c => c.toLowerCase().includes("talhão") || c.toLowerCase().includes("talhao"));
  const talhaoInformado = idxTalhao !== -1 ? String(valoresLinha[idxTalhao]).trim() : "";

  const dadosSatelite = buscarUltimaQuinzenaBigQuery(talhaoInformado);

  let contextoChamado = "";
  for (let i = 0; i < cabecalhos.length; i++) {
    if (valoresLinha[i] !== "" && i !== 15) {
      contextoChamado += cabecalhos[i] + ": " + valoresLinha[i] + " | ";
    }
  }

  const prompt = "Gere uma sugestão de resposta ao produtor rural em linguagem simples, direta e acolhedora, sem jargões técnicos.\n" +
    "Cruze a dúvida do chamado com os dados de satélite abaixo.\n" +
    "Se constar 'Aguardando processamento de satélite', informe educadamente que os dados daquele talhão ainda estão sendo processados.\n\n" +
    "Dados do chamado:\n" + contextoChamado + "\n\n" +
    "Dados de satélite (última quinzena):\n" + dadosSatelite;

  const respostaIA = chamarGemini(prompt);
  sheet.getRange(linha, 16).setValue(respostaIA);
}

function buscarUltimaQuinzenaBigQuery(talhaoId) {
  if (!talhaoId) return "Aguardando processamento de satélite";
  
  const talhaoLimpo = talhaoId.replace(/'/g, "");
  const query = "SELECT * EXCEPT(data_inicio) FROM `agrovale-gcp.gold.fato_quinzenal` WHERE TRIM(talhao_id) = '" + talhaoLimpo + "' ORDER BY data_inicio DESC LIMIT 1";

  const queryResults = BigQuery.Jobs.query({ query: query, useLegacySql: false }, "agrovale-gcp");
  
  if (!queryResults.rows || queryResults.rows.length === 0) {
    return "Aguardando processamento de satélite";
  }

  const fields = queryResults.schema.fields;
  const row = queryResults.rows[0].f;
  let resumo = [];
  for (let i = 0; i < fields.length; i++) {
    if (row[i].v !== null) {
      resumo.push(fields[i].name + ": " + row[i].v);
    }
  }
  return resumo.join(" | ");
}

function chamarGemini(prompt) {
  const modelos = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"];
  const options = {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify({ contents: [{ parts: [{ text: prompt }] }] }),
    muteHttpExceptions: true
  };

  for (let tentativa = 1; tentativa <= 2; tentativa++) {
    for (let i = 0; i < modelos.length; i++) {
      const url = "https://generativelanguage.googleapis.com/v1beta/models/" + modelos[i] + ":generateContent?key=" + GEMINI_API_KEY;
      const response = UrlFetchApp.fetch(url, options);
      const json = JSON.parse(response.getContentText());

      if (json.candidates && json.candidates[0] && json.candidates[0].content) {
        return json.candidates[0].content.parts[0].text.trim();
      }
      console.error("Tentativa " + tentativa + " - Falha no modelo " + modelos[i] + ":", JSON.stringify(json));
    }
    Utilities.sleep(2000);
  }

  return "Erro temporário ao gerar resposta automática.";
}
