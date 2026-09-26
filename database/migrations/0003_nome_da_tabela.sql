-- 0003_nome_da_tabela.sql — nome da tabela (view) de cada arquivo no banco da estação.
-- Por padrão é o id do registro (file_key); o usuário pode dar um nome melhor no
-- registro (ex.: um padrão dinâmico criado a partir de um arquivo concreto herdaria a
-- data no nome). A ingestão grava aqui o nome em uso.
ALTER TABLE monitored_files ADD COLUMN IF NOT EXISTS table_name TEXT;
UPDATE monitored_files SET table_name = file_key WHERE table_name IS NULL;
