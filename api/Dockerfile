# Imagem da API de dados (FastAPI + asyncpg). Build context = raiz do repositório.
FROM python:3.12-slim

WORKDIR /app
COPY api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY api/app/ /app/app/

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
# 2 workers: o host tem 4 vCPU compartilhados com banco, ingestão e dashboard.
# --proxy-headers só confia em TRUSTED_PROXY_IPS (padrão: nenhum → o IP visto é o do cliente/proxy)
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers --forwarded-allow-ips \"${TRUSTED_PROXY_IPS:-127.0.0.1}\""]
