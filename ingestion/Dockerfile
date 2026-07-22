# Imagem do serviço de ingestão (inclui a biblioteca de QA/QC de processing/).
# ATENÇÃO: o build context é a RAIZ do repositório (ver docker-compose.yml) —
# necessário para copiar processing/ junto.
FROM python:3.12-slim

WORKDIR /app

# Dependências primeiro (melhor cache de build).
COPY ingestion/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código.
COPY ingestion/ingest.py .
COPY ingestion/parsers ./parsers
COPY processing/__init__.py processing/pipeline.py ./processing/
COPY processing/checks ./processing/checks

# config.yaml (ingestão), processing/config.yaml (QA/QC) e os diretórios de dados
# entram por volume (ver docker-compose.yml).
ENV PYTHONUNBUFFERED=1

CMD ["python", "ingest.py"]
