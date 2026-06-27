# Manutenção

Operação contínua do servidor central: logs, backup/restore, dados em quarentena e
solução de problemas.

## Logs

```bash
cd server
docker compose logs -f ingestion        # ingestão (parse/carga)
docker compose logs -f ftp              # conexões FTP das estações
docker compose logs -f db               # banco

# Nas estações:
tail -f ~/.carbonara/collector.log      # ou /var/log/carbonara-collector.log
```

## Diretórios de dados (no servidor)

| Diretório | Conteúdo | Ação |
|-----------|----------|------|
| `data/landing/<station>/` | Arquivos recebidos, aguardando ingestão | Esvazia sozinho ao processar |
| `data/archive/<station>/<data>/` | Arquivos já ingeridos (auditoria) | Pode ser arquivado/rotacionado |
| `data/quarantine/<station>/` | Arquivos que **falharam** no parse | Investigar e reprocessar |

### Reprocessar arquivos em quarentena
Depois de corrigir o parser/config, devolva o arquivo ao landing:
```bash
mv data/quarantine/flona01/arquivo.csv data/landing/flona01/
docker compose logs -f ingestion
```

### Rotacionar o archive (evitar disco cheio)
```bash
# Exemplo: compactar arquivos com mais de 90 dias.
find data/archive -type f -mtime +90 -name '*.csv' -exec gzip {} \;
# Ou mover para armazenamento externo/objeto conforme a política do projeto.
```

## Backup do banco (TimescaleDB)

### Dump lógico (simples, recomendado para começar)
```bash
docker exec -t carbonara-db pg_dump -U carbonara -Fc carbonara \
  > backup_carbonara_$(date +%Y%m%d).dump
```

### Restore
```bash
# Em um banco vazio (recria estrutura + dados):
docker exec -i carbonara-db pg_restore -U carbonara -d carbonara --clean \
  < backup_carbonara_YYYYMMDD.dump
```

> Automatize com um cron na workstation (diário/semanal) e guarde os dumps **fora** da
> máquina. Para volumes grandes, avalie `pg_basebackup`/backup contínuo.

## Saúde dos serviços

```bash
docker compose ps                       # estado/health de cada container
docker stats --no-stream                # uso de CPU/memória
df -h                                   # espaço em disco (crítico p/ landing/archive/db)
```

## Banco: consultas úteis

```sql
-- Volume de dados e última medição por estação.
SELECT station_id, count(*) AS n, max(time) AS ultima
FROM measurements GROUP BY station_id ORDER BY ultima DESC;

-- Estações sem dados recentes (possível falha de coleta).
SELECT station_id, max(time) AS ultima
FROM measurements GROUP BY station_id
HAVING max(time) < now() - INTERVAL '2 hours';

-- Hypertables e tamanho em disco.
SELECT * FROM timescaledb_information.hypertables;
SELECT hypertable_name, pg_size_pretty(hypertable_size(format('%I', hypertable_name)::regclass))
FROM timescaledb_information.hypertables;
```

## Troubleshooting

| Sintoma | Causa provável | O que fazer |
|---------|----------------|-------------|
| Arquivos param no `landing/` | Ingestão parada ou erro no parser | `docker compose logs ingestion`; checar `config.yaml` |
| Arquivos indo para `quarantine/` | Formato/colunas não batem com o parser | Ver o log do erro; ajustar `columns`/`timestamp_format` |
| Estação não envia | Credenciais/host FTP errados ou rede | `collector.py --dry-run`; testar conectividade; ver log do agente |
| `measurements` não cresce | Duplicatas (mesma chave natural) ou nada novo | Esperado se reingerindo; conferir timestamps |
| Disco cheio | `archive/` ou banco crescendo | Rotacionar archive; verificar compressão do TimescaleDB |
| Banco não sobe / init não rodou | Volume `db_data` já existia | Scripts `init/` só rodam em banco novo; aplicar mudanças via migração manual |

## Aplicar mudanças de schema depois do primeiro start

Os scripts em `database/init/` só rodam **uma vez** (banco vazio). Para alterar o schema
de um banco já em uso, rode o SQL manualmente:
```bash
docker exec -i carbonara-db psql -U carbonara -d carbonara < database/init/02_schema.sql
```
(Os arquivos são idempotentes — `IF NOT EXISTS` / `ON CONFLICT`.)
