-- 0005_ledger_head_len.sql — quantos bytes a assinatura do início do arquivo cobre.
-- Sem isso, um arquivo menor que 64 kB mudava de assinatura a cada append e a ingestão
-- "reiniciava do zero" a cada ciclo.
ALTER TABLE ingest_files ADD COLUMN IF NOT EXISTS head_len INTEGER;
