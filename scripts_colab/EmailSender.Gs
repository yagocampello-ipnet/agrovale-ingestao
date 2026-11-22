function enviarRespostaSuporte(e) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName("Form Responses 1") || ss.getSheetByName("Form_Responses") || ss.getSheets()[0];


  if (!sheet) {
    Logger.log("Nenhuma aba encontrada na planilha.");
    return;
  }


  // Garante os cabeçalhos das colunas de controle (J até O)
  if (sheet.getRange(1, 10).getValue() === "") sheet.getRange(1, 10).setValue("Status Email");
  if (sheet.getRange(1, 11).getValue() === "") sheet.getRange(1, 11).setValue("Link da Thread (Gmail)");
  if (sheet.getRange(1, 12).getValue() === "") sheet.getRange(1, 12).setValue("Timestamp Última Ação");
  if (sheet.getRange(1, 13).getValue() === "") sheet.getRange(1, 13).setValue("Status da Thread");
  if (sheet.getRange(1, 14).getValue() === "") sheet.getRange(1, 14).setValue("Thread ID Interno");
  if (sheet.getRange(1, 15).getValue() === "") sheet.getRange(1, 15).setValue("Status do Chamado");


  // Se veio de uma edição manual na Coluna G (7 = Answer)
  if (e && e.range) {
    if (e.range.getSheet().getName() !== sheet.getName() || e.range.getColumn() !== 7 || e.range.getRow() <= 1) {
      return;
    }
    processarLinha(sheet, e.range.getRow());
    return;
  }


  // Execução via Timer (1 em 1 minuto) ou manual
  var ultimaLinha = sheet.getLastRow();
  if (ultimaLinha < 2) return;
  for (var linha = 2; linha <= ultimaLinha; linha++) {
    processarLinha(sheet, linha);
  }
}


function processarLinha(sheet, linha) {
  var dados = sheet.getRange(linha, 1, 1, 15).getValues()[0];
  var talhao = dados[1];
  var motivo = dados[2];
  var duvida = dados[3];
  var emailCliente = dados[5];
  var resposta = dados[6] ? dados[6].toString().trim() : "";
  var statusEnvio = dados[9] ? dados[9].toString() : "";    
  var ticketFechado = dados[14] ? dados[14].toString().trim().toLowerCase() : "";


  if (ticketFechado === "fechado") return;
  if (resposta === "" || resposta.indexOf("#") === 0 || resposta.toLowerCase() === "loading..." || resposta.toLowerCase() === "carregando...") return;
  if (!emailCliente || emailCliente.toString().indexOf("@") === -1) return;
  if (statusEnvio.indexOf("Enviado") !== -1) return;


  var emailLimpo = emailCliente.toString().trim();


  // Gera um ID único por chamado (ex: Ticket #0002-841) para criar sempre uma thread nova
  var numChamado = ("000" + (linha - 1)).slice(-4);
  var sufixoUnico = new Date().getTime().toString().slice(-3);
  var codigoTicket = "Ticket #" + numChamado + "-" + sufixoUnico;


  var assunto = "[" + codigoTicket + "] Retorno do Suporte Técnico - Talhão: " + talhao;


// O e-mail enviará EXATAMENTE o que estiver escrito na coluna "Answer" da planilha
  var corpoEmail = resposta;


  // 1. CORREÇÃO DE INDEXAÇÃO: Dispara usando GmailApp para criação imediata da thread no perfil da busca
  GmailApp.sendEmail(emailLimpo, assunto, corpoEmail, {
    cc: "suportegeoanalise@ipnet.cloud"
  });


  var dataHora = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "dd/MM/yyyy HH:mm:ss");
  sheet.getRange(linha, 10).setValue("Enviado em " + dataHora);
  SpreadsheetApp.flush();


  // 2. CORREÇÃO DE TEMPO: Aguarda 5 segundos para que os servidores do Gmail consolidem a thread
  Utilities.sleep(5000);
 
  var queryBusca = 'to:' + emailLimpo + ' subject:"' + codigoTicket + '"';
  var threads = GmailApp.search(queryBusca, 0, 1);


  if (threads.length > 0) {
    var threadId = threads[0].getId();
    var linkGmail = "https://mail.google.com/mail/u/0/#inbox/" + threadId;
   
    // Limpa qualquer formatação pré-existente
    sheet.getRange(linha, 11).clearDataValidations().clearFormat();
   
    // Insere a URL limpa (o Sheets renderiza como link automaticamente, sem XSS)
    sheet.getRange(linha, 11).setValue(linkGmail);
   
    sheet.getRange(linha, 12).setValue(dataHora);
    sheet.getRange(linha, 13).setValue("Aguardando cliente");
    sheet.getRange(linha, 14).setValue(threadId);


    if (sheet.getRange(linha, 15).getValue() === "") {
      sheet.getRange(linha, 15).setValue("Aberto");
    }
  } else {
    // Falha de indexação segura
    sheet.getRange(linha, 11).setValue("Processando...");
  }
}
