# Imagem do serviço de processamento (QA/QC).
FROM python:3.12-slim

WORKDIR /app

# Dependências primeiro (melhor cache de build).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código.
COPY process.py .
COPY checks ./checks

# config.yaml entra por volume (ver docker-compose.yml).
ENV PYTHONUNBUFFERED=1

CMD ["python", "process.py"]
