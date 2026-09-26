"""Configuração da API — tudo por variável de ambiente (ver docker-compose.yml / .env)."""
from __future__ import annotations

import os

API_VERSION = "1.0.0"

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_USER = os.environ.get("DB_USER", "carbonara")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_NAME = os.environ.get("DB_NAME", "carbonara")

# "Pepper" dos hashes das chaves: só o hash fica no banco; sem o pepper, um dump do
# banco não permite forjar chaves. Vem do .env (gerado pelo setup-db-host.sh).
API_KEY_PEPPER = os.environ.get("API_KEY_PEPPER", "")

# Limites
ANON_RATE_LIMIT = int(os.environ.get("API_ANON_RATE_LIMIT", "60"))       # req/min por IP
MAX_RAW_POINTS = int(os.environ.get("API_MAX_RAW_POINTS", "200000"))     # por resposta
DEFAULT_RAW_POINTS = int(os.environ.get("API_DEFAULT_RAW_POINTS", "20000"))
MAX_AGG_BUCKETS = int(os.environ.get("API_MAX_AGG_BUCKETS", "100000"))
KEY_CACHE_SECONDS = 15      # revogação/expiração valem em até 15 s em cada worker
