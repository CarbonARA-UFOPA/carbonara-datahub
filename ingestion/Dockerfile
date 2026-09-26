# Imagem do serviço de ingestão. Inclui a biblioteca de QA/QC (processing/) e o
# pacote de parse compartilhado com a dashboard (carbonara_parse/).
# ATENÇÃO: o build context é a RAIZ do repositório (ver docker-compose.yml).
FROM python:3.12-slim

WORKDIR /app

# Dependências primeiro (melhor cache de build).
COPY ingestion/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Código: ingestão + QC + parse compartilhado (o mesmo da dashboard).
COPY ingestion/ /app/
COPY processing/ /app/processing/
COPY carbonara_parse/ /app/carbonara_parse/

# config.yaml da dashboard (registro dos arquivos monitorados), processing/config.yaml
# (QC global) e a raiz de dados (somente leitura) entram por volume (ver docker-compose.yml).
ENV PYTHONUNBUFFERED=1 \
    DASHBOARD_CONFIG=/app/dashboard-config.yaml \
    PROCESSING_CONFIG=/app/processing/config.yaml

CMD ["python", "ingest.py"]
