# Imagem do serviço de ingestão.
FROM python:3.12-slim

WORKDIR /app

# Dependências primeiro (melhor cache de build).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código.
COPY ingest.py .
COPY parsers ./parsers

# config.yaml e os diretórios de dados entram por volume (ver docker-compose.yml).
ENV PYTHONUNBUFFERED=1

CMD ["python", "ingest.py"]
