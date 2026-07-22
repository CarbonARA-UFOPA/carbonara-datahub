# Processamento (QA/QC) — `processing/`

Quarta camada da arquitetura: **tratamento dos dados já ingeridos**. Roda passadas
periódicas de controle de qualidade sobre as medições recém-inseridas, marcando a coluna
`measurements.quality` com flags. Lê e escreve **apenas no banco** — não conhece arquivos,
diretórios nem transporte (a ingestão continua inserindo os dados crus; nada aqui altera
`value`, só `quality`).

```
collection/  → transporte      (arquivo chega)
ingestion/   → parse + insert  (arquivo vira linha, crua)
processing/  → QA/QC           (linha ganha flag de qualidade)   ← você está aqui
database/    → armazenamento
```

## Convenção de flags (`measurements.quality`)

| Flag | Significado | Atribuído por |
|------|-------------|---------------|
| `0`  | ok / ainda não avaliado | default |
| `1`  | valor inválido no parse (ex.: não numérico) | ingestão |
| `2`  | fora da faixa física válida | `range_check` |
| `3+` | reservado (spike, sensor travado, calibração, ...) | regras futuras |

Consumidores devem filtrar por `quality = 0` para usar só dados aprovados. Nenhuma
medição é apagada ou alterada — o valor cru permanece, auditável.

## Como funciona

- O loop (`process.py`) mantém uma **marca-d'água** (`processing_state.qc_watermark`,
  tabela criada pelo próprio serviço): a maior `ingested_at` já avaliada. Cada passada
  avalia só a janela nova e avança a marca se **todos** os checks passarem.
- Os checks são **idempotentes**: só tocam linhas com `quality = 0`, então reavaliar a
  mesma janela (após uma falha, ou reprocessamento manual) não corrompe nada.
- Falha em um check não derruba o serviço: rollback, log, e a janela é reavaliada na
  próxima passada (mesmo espírito do isolamento por arquivo da ingestão).

## Configuração

```bash
cp processing/config.example.yaml processing/config.yaml   # e ajuste as faixas
docker compose up -d --build processing
docker compose logs -f processing                          # "check range_check: N sinalizadas"
```

As faixas do `range_check` são **limites físicos plausíveis**, generosos de propósito:
o objetivo é pegar sensor defeituoso e valor absurdo, não recortar extremos reais.

## Adicionando uma regra nova

Mesmo padrão dos parsers da ingestão:

1. Crie `checks/minha_regra.py` com uma subclasse de `Check`, decorada com
   `@register("minha_regra")`, implementando `run(conn, since) -> int` (set-based,
   só linhas `quality = 0`).
2. Importe o módulo em `checks/__init__.py` (o import dispara o `@register`).
3. Referencie no `config.yaml` sob `checks:` com um flag próprio (documente-o na
   tabela acima e em `checks/base.py`).

`process.py` não muda.

## Reprocessar do zero

```sql
-- zera a marca-d'água (próxima passada reavalia tudo):
DELETE FROM processing_state WHERE key = 'qc_watermark';
-- opcional: limpa flags de faixa para reavaliar com limites novos:
UPDATE measurements SET quality = 0 WHERE quality = 2;
```
