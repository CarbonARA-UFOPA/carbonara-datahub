-- 0006_valor_texto.sql — variáveis do tipo TEXTO no banco (nº de série de uma sonda,
-- modelo do sensor…). measurements.value é numérico; o texto fica em value_text, com
-- value nulo e quality 0. Sem isso, uma coluna como SERIAL (o que identifica cada
-- lançamento de radiossonda) era descartada pela ingestão.
-- Coluna anulável e sem default: não reescreve chunks (vale também para os comprimidos).
ALTER TABLE measurements ADD COLUMN IF NOT EXISTS value_text TEXT;
