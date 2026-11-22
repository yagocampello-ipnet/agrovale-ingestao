function verificarRespostasClientes() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName("Form Responses 1") || ss.getSheetByName("Form_Responses") || ss.getSheets()[0];
 
  if (!sheet) {
    Logger.log("Aba não encontrada.");
    return;
  }


  var ultimaLinha = sheet.getLastRow();
  if (ultimaLinha < 2) {
    Logger.log("Nenhum ticket para verificar.");
    return;
  }


  // Lê da Coluna A (1) até a Coluna O (15) para incluir a validação do Dropdown
  var dados = sheet.getRange(2, 1, ultimaLinha - 1, 15).getValues();


  for (var i = 0; i < dados.length; i++) {
    var emailCliente = dados[i][5] ? dados[i][5].toString().trim().toLowerCase() : ""; // Coluna F
    var statusAtual = dados[i][12] ? dados[i][12].toString().trim() : "";               // Coluna M
    var threadId = dados[i][13] ? dados[i][13].toString().trim() : "";                  // Coluna N
    var ticketFechado = dados[i][14] ? dados[i][14].toString().trim().toLowerCase() : ""; // Coluna O (Dropdown)
    var linhaPlanilha = i + 2;


    // 🔴 NOVA REGRA: Se o chamado foi marcado como Fechado, pula para a próxima linha
    if (ticketFechado === "fechado") {
      continue;
    }


    if (threadId !== "" && statusAtual !== "Chamado Encerrado") {
      try {
        var thread = GmailApp.getThreadById(threadId);
        if (!thread) continue;


        var mensagens = thread.getMessages();
        var ultimaMensagem = mensagens[mensagens.length - 1];


        var remetente = ultimaMensagem.getFrom().toLowerCase();
        var dataUltimaMsg = ultimaMensagem.getDate();
        var timestampFormatado = Utilities.formatDate(dataUltimaMsg, Session.getScriptTimeZone(), "dd/MM/yyyy HH:mm:ss");


        // Se o remetente da última mensagem for o cliente e o ticket não estiver fechado, marca a cobrança
        if (emailCliente !== "" && remetente.indexOf(emailCliente) !== -1) {
          sheet.getRange(linhaPlanilha, 12).setValue(timestampFormatado);
          sheet.getRange(linhaPlanilha, 13).setValue("FALTA RESPONDER (Cliente enviou nova msg)");
          Logger.log("Linha " + linhaPlanilha + ": Cliente respondeu em " + timestampFormatado);
        } else {
          sheet.getRange(linhaPlanilha, 12).setValue(timestampFormatado);
          sheet.getRange(linhaPlanilha, 13).setValue("Última resposta: Nossa equipe");
          Logger.log("Linha " + linhaPlanilha + ": Aguardando cliente (última ação nossa em " + timestampFormatado + ")");
        }
      } catch (err) {
        Logger.log("Erro na linha " + linhaPlanilha + " (Thread ID: " + threadId + "): " + err.message);
      }
    }
  }
}

