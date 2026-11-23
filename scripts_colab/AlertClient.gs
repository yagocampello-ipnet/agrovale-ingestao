const CONFIG = {
  // Configurações de envio de e-mail:
  EMAIL_CLIENTE: '',
  EMAIL_CC: 'suportegeoanalise@ipnet.cloud',
  ASSUNTO_EMAIL: '🔴 Alerta Agronômico - Dashboard Powered by IAgro',

  // Limiares calibrados com o Dashboard:
  LIMIAR_DESVIO_NDVI_CRITICO: -10,      // Menos de -10% vs histórico (estresse foliar)
  LIMIAR_ANOMALIA_CHUVA_QUINZENA: -10,  // Déficit na quinzena maior que 10mm
  LIMIAR_DEFICIT_CHUVA_ACUMULADO: -40,  // Mais de 40mm de déficit acumulado no ciclo
  LIMIAR_CHANCE_VERANICO: 50            // Limiar de 50% para veranico
};

function verificarAlertasAgronomicos() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const fatoSheet = ss.getSheetByName('fato_quinzenal');
  const veranicoSheet = ss.getSheetByName('risco_veranico');

  if (!fatoSheet || !veranicoSheet) {
    throw new Error('Abas "fato_quinzenal" ou "risco_veranico" não encontradas na planilha.');
  }

  const fatoRows = getSheetObjects_(fatoSheet);
  const veranicoRows = getSheetObjects_(veranicoSheet);

  const talhoes = [...new Set(fatoRows.map(r => r.talhao_id).filter(Boolean))];
  const alertasPorTalhao = [];

  // Mês atual do calendário (ex: Novembro = 11)
  const mesAtualCalendario = new Date().getMonth() + 1;

  console.log(`[Início] Mês Atual: ${mesAtualCalendario} | Talhões: ${talhoes.join(', ')}`);

  talhoes.forEach(talhaoId => {
    const alertas = [];

    // 1. Obter safra mais recente e última quinzena
    const rowsTalhao = fatoRows.filter(r => r.talhao_id === talhaoId && r.safra);
    if (rowsTalhao.length === 0) return;

    const safrasOrdenadas = [...new Set(rowsTalhao.map(r => String(r.safra)))].sort().reverse();
    const safraAtual = safrasOrdenadas[0];

    const quinzenasSafra = rowsTalhao
      .filter(r => String(r.safra) === safraAtual)
      .sort((a, b) => Number(a.quinzena_milho || 0) - Number(b.quinzena_milho || 0));

    const ultimaQ = quinzenasSafra[quinzenasSafra.length - 1];
    if (!ultimaQ) return;

    console.log(`[Talhão ${talhaoId}] Safra: ${safraAtual} | Última Quinzena: #${ultimaQ.quinzena_milho}`);

    // --- CARTÃO 1: VIGOR VEGETATIVO (NDVI) ---
    const emMaturacao = String(ultimaQ.estadio_fenologico || '').toLowerCase().includes('maturacao');
    let desvioNdviPct = ultimaQ.Desvio_NDVI_Pct;
    if (desvioNdviPct === '' || desvioNdviPct === null || desvioNdviPct === undefined) {
      if (ultimaQ.NDVI_interpolado !== '' && ultimaQ.NDVI_Normal_8safras) {
        desvioNdviPct = ((Number(ultimaQ.NDVI_interpolado) - Number(ultimaQ.NDVI_Normal_8safras)) / Number(ultimaQ.NDVI_Normal_8safras)) * 100;
      }
    }
    if (!emMaturacao && desvioNdviPct !== '' && Number(desvioNdviPct) <= CONFIG.LIMIAR_DESVIO_NDVI_CRITICO) {
      alertas.push(
        `• 🔴 Vigor Crítico (NDVI): ${Number(ultimaQ.NDVI_interpolado).toFixed(3)} (${Number(desvioNdviPct).toFixed(1)}% vs média esperada)`
      );
    }

    // --- CARTÃO 2: BALANÇO DE CHUVA (CHIRPS) ---
    const anomaliaQuinzena = Number(ultimaQ.Anomalia_Chuva_mm || 0);
    const anomaliaAcum = Number(ultimaQ.Anomalia_Chuva_Acumulada_mm || 0);
    if (anomaliaQuinzena < CONFIG.LIMIAR_ANOMALIA_CHUVA_QUINZENA || anomaliaAcum <= CONFIG.LIMIAR_DEFICIT_CHUVA_ACUMULADO) {
      alertas.push(
        `• 🔴 Déficit Hídrico: ${Number(ultimaQ.Chuva_mm || 0).toFixed(1)} mm na quinzena (${anomaliaQuinzena >= 0 ? '+' : ''}${anomaliaQuinzena.toFixed(1)} mm vs normal | Acumulado: ${anomaliaAcum.toFixed(1)} mm)`
      );
    }

    // --- CARTÃO 3: TEMPERATURA & CALOR (MODIS LST) ---
    const alertaTermico = ultimaQ.Alerta_Estresse_Termico === true ||
      String(ultimaQ.Alerta_Estresse_Termico).toUpperCase() === 'TRUE' ||
      Number(ultimaQ.Alerta_Estresse_Termico) === 1;
    if (alertaTermico) {
      alertas.push(
        `• 🔴 Calor Excessivo: ${Number(ultimaQ.Temp_Dia_C).toFixed(1)}°C diurno (Alerta de estresse térmico ativo)`
      );
    }

    // --- CARTÃO 4: RISCO DE VERANICO (Janela de 1 mês no mês atual) ---
    // Faz a correspondência flexível do talhão na aba risco_veranico
    let veranicoMes = veranicoRows.filter(r => 
      String(r.talhao_id).trim().toLowerCase() === String(talhaoId).trim().toLowerCase() &&
      Number(r.janela_meses) === 1 &&
      Number(r.mes_inicio) === mesAtualCalendario
    );

    // Fallback caso a aba use um identificador genérico
    if (veranicoMes.length === 0) {
      veranicoMes = veranicoRows.filter(r => 
        Number(r.janela_meses) === 1 &&
        Number(r.mes_inicio) === mesAtualCalendario
      );
    }

    if (veranicoMes.length > 0) {
      const getChance = (dias) => {
        const item = veranicoMes.find(r => Number(r.x_dias) === dias);
        return item ? Number(item.chance_pct || 0) : 0;
      };

      const pct7 = getChance(7);
      const pct10 = getChance(10);
      const pct15 = getChance(15);

      console.log(` > Card 4 (Novembro/Mês ${mesAtualCalendario}): 7+d = ${pct7}% | 10+d = ${pct10}% | 15+d = ${pct15}%`);

      // Dispara apenas se 7+, 10+ e 15+ dias estiverem acima de 50%
      const veranicoCritico = pct7 > CONFIG.LIMIAR_CHANCE_VERANICO && 
                              pct10 > CONFIG.LIMIAR_CHANCE_VERANICO && 
                              pct15 > CONFIG.LIMIAR_CHANCE_VERANICO;

      if (veranicoCritico) {
        alertas.push(
          `• 🔴 Alto Risco de Veranico (Janela de 1 Mês - Mês ${mesAtualCalendario}): Todas as durações acima de 50% (7+ dias: ${pct7.toFixed(0)}% | 10+ dias: ${pct10.toFixed(0)}% | 15+ dias: ${pct15.toFixed(0)}%)`
        );
      }
    }

    if (alertas.length > 0) {
      alertasPorTalhao.push({
        talhaoId,
        safraAtual,
        quinzena: ultimaQ.quinzena_milho,
        estadio: ultimaQ.estadio_fenologico || '—',
        alertas,
      });
    }
  });

  // Envio do e-mail de alerta (sem link da planilha)
  if (alertasPorTalhao.length > 0) {
    let corpo = 'Olá,\n\nForam detectados indicadores em estado CRÍTICO no monitoramento agronômico (Dashboard Powered by IAgro):\n\n';

    alertasPorTalhao.forEach(item => {
      corpo += `Talhão: ${item.talhaoId} | Safra: ${item.safraAtual} | Quinzena Atual #${item.quinzena}\n`;
      corpo += `Fase Fenológica: ${item.estadio}\n`;
      corpo += item.alertas.join('\n') + '\n\n';
    });

    corpo += 'Verificação automática do sistema.';

    console.log(`[Disparo] Enviando e-mail para: ${CONFIG.EMAIL_CLIENTE}`);
    MailApp.sendEmail({
      to: CONFIG.EMAIL_CLIENTE,
      cc: CONFIG.EMAIL_CC,
      subject: CONFIG.ASSUNTO_EMAIL,
      body: corpo,
    });
    console.log('[Sucesso] E-mail de alerta enviado com sucesso.');
  } else {
    console.log('[Finalizado] Nenhum indicador em nível crítico detectado (Novembro não atinge o critério de veranico > 50%).');
  }
}

function getSheetObjects_(sheet) {
  const values = sheet.getDataRange().getValues();
  if (values.length < 2) return [];
  const headers = values[0];
  return values.slice(1).map(row => {
    const obj = {};
    headers.forEach((h, i) => {
      if (h) obj[h] = row[i];
    });
    return obj;
  });
}
