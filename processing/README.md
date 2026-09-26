# Processamento (QA/QC) — `processing/`

Camada de **tratamento e qualificação dos dados**, aplicada **entre o parse e o
insert**: para cada medição parseada pela ingestão, o pipeline de QC calcula o flag de
qualidade **em memória**, e a linha já entra em `measurements` com `quality` definido.
Não é um serviço separado — é uma biblioteca importada pelo container da ingestão.

```
collection/  → transporte      (arquivo chega)
ingestion/   → parse           (arquivo vira medições)
processing/  → QA/QC           (medição ganha flag, antes do insert)   ← você está aqui
database/    → armazenamento   (linha já entra qualificada)
```

Nenhuma medição é descartada ou alterada: o valor cru **sempre** entra no banco,
auditável — reprovada só significa `quality != 0`.

## Convenção de flags (`measurements.quality`)

| Flag | Significado | Atribuído por |
|------|-------------|---------------|
| `0`  | ok | — |
| `1`  | valor inválido no parse (ex.: não numérico) | parser (ingestão) |
| `2`  | fora da faixa física válida | `range_check` |
| 3 | salto maior que o aceitável entre leituras consecutivas (`qc_spike` no Tratamento) | `spike_check` |
| 4 | valor travado: N leituras iguais seguidas (`qc_stuck` no Tratamento) | `persistence_check` |
| `3+` | reservado (spike, sensor travado, calibração, ...) | regras futuras |

Consumidores devem filtrar por `quality = 0` para usar só dados aprovados.

## Como funciona

- A ingestão monta um `QualityPipeline` (de `processing/config.yaml`) na partida e
  chama `evaluate(m)` para cada medição; o retorno vai direto na coluna `quality`.
- Um flag do parse (`quality != 0`) nunca é sobrescrito; o **primeiro** check violado
  decide o flag — ordene os checks no config do mais grave para o menos grave.
- Sem `processing/config.yaml` (ou sem `checks:` nele), o QC fica desativado — a
  ingestão loga um aviso e segue inserindo com o flag do parse.
- Como o insert é idempotente (`ON CONFLICT DO NOTHING`), medições já inseridas **não**
  são reavaliadas: mudanças de regra valem para dados novos. Para reavaliar histórico,
  reprocessa-se os arquivos de `archive/` (copie-os de volta para `incoming/`).

## Configuração

```bash
cp processing/config.example.yaml processing/config.yaml   # e ajuste as faixas
docker compose up -d --build ingestion                     # o QC roda dentro da ingestão
docker compose logs -f ingestion                           # "... N com flag de qualidade"
```

As faixas do `range_check` são **limites físicos plausíveis**, generosos de propósito:
o objetivo é pegar sensor defeituoso e valor absurdo, não recortar extremos reais.

## Adicionando uma regra nova

Mesmo padrão dos parsers da ingestão:

1. Crie `checks/minha_regra.py` com uma subclasse de `Check`, decorada com
   `@register("minha_regra")`, implementando `violates(measurement) -> bool` e um
   `default_flag` próprio (documente-o na tabela acima e em `checks/base.py`).
2. Importe o módulo em `checks/__init__.py` (o import dispara o `@register`).
3. Referencie no `config.yaml` sob `checks:`.
4. `docker compose up -d --build ingestion` (o código entra na imagem da ingestão).

Nem `pipeline.py` nem `ingest.py` mudam.
