// 1. Roda automaticamente toda vez que um novo ticket chega do Google Forms
function criarDropdownNovoTicket(e) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName("Form Responses 1") || ss.getSheetByName("Form_Responses") || ss.getSheets()[0];
  if (!sheet) return;


  // Garante o cabeçalho na Coluna O (15)
  if (sheet.getRange(1, 15).getValue() === "") {
    sheet.getRange(1, 15).setValue("Status do Chamado");
  }


  // Descobre qual linha acabou de ser inserida pelo Forms
  var linhaNova = (e && e.range) ? e.range.getRow() : sheet.getLastRow();
  if (linhaNova <= 1) return;


  aplicarRegraDropdownNaCelula(sheet, linhaNova);
}


// 2. Rode esta função UMA VEZ manualmente para colocar o dropdown nas linhas que já existem
function aplicarDropdownEmTodasAsLinhas() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName("Form Responses 1") || ss.getSheetByName("Form_Responses") || ss.getSheets()[0];
  if (!sheet) return;


  if (sheet.getRange(1, 15).getValue() === "") {
    sheet.getRange(1, 15).setValue("Status do Chamado");
  }


  var ultimaLinha = sheet.getLastRow();
  for (var linha = 2; linha <= ultimaLinha; linha++) {
    aplicarRegraDropdownNaCelula(sheet, linha);
  }
}


// Função auxiliar que monta o dropdown "Aberto" / "Fechado" na Coluna O (15)
function aplicarRegraDropdownNaCelula(sheet, linha) {
  var celula = sheet.getRange(linha, 15); // Coluna O


  var regraDropdown = SpreadsheetApp.newDataValidation()
    .requireValueInList(["Aberto", "Fechado"], true)
    .setAllowInvalid(false)
    .build();


  celula.setDataValidation(regraDropdown);


  // Se a célula estiver vazia, já inicia marcada como "Aberto"
  if (celula.getValue() === "") {
    celula.setValue("Aberto");
  }
}
