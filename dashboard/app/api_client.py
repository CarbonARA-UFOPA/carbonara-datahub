"""Cliente mínimo da API de dados (bloco ``api:`` do config.yaml: ``url`` e ``key``).

Só urllib — a dashboard não ganha dependências por isso. Toda chamada tem timeout
curto e levanta ``ApiError``; quem chama decide o fallback (os gráficos voltam a ler o
arquivo, a página de chaves mostra o erro). Nunca derruba uma página.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .i18n import gettext as _

DEFAULT_TIMEOUT = 8.0


class ApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def configured(cfg: dict) -> bool:
    api = cfg.get("api") or {}
    return bool((api.get("url") or "").strip())


def base_url(cfg: dict) -> str:
    return (cfg.get("api") or {}).get("url", "").strip().rstrip("/")


def public_url(cfg: dict) -> str:
    """URL a mostrar para os usuários (``public_url`` se definida, senão ``url``)."""
    api = cfg.get("api") or {}
    return (api.get("public_url") or api.get("url") or "").strip().rstrip("/")


def _request(cfg: dict, method: str, path: str, params: dict | None = None,
             body: dict | None = None, timeout: float = DEFAULT_TIMEOUT, raw_bytes: bool = False):
    if not configured(cfg):
        raise ApiError(_("API não configurada (Configurações → API de dados)."))
    url = base_url(cfg) + path
    if params:
        q = {k: v for k, v in params.items() if v is not None and v != ""}
        if q:
            url += "?" + urllib.parse.urlencode(q)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    key = (cfg.get("api") or {}).get("key")
    if key:
        req.add_header("X-API-Key", key)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if raw_bytes:
                return raw
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("detail")
        except Exception:  # noqa: BLE001
            detail = None
        raise ApiError(_("API respondeu %(codigo)s: %(detalhe)s", codigo=exc.code,
                         detalhe=detail or exc.reason), exc.code) from exc
    except urllib.error.URLError as exc:
        raise ApiError(_("API inacessível em %(url)s: %(motivo)s", url=base_url(cfg), motivo=exc.reason)) from exc
    except (TimeoutError, OSError, ValueError) as exc:
        raise ApiError(_("falha ao falar com a API: %(erro)s", erro=exc)) from exc


def get(cfg: dict, path: str, params: dict | None = None, timeout: float = DEFAULT_TIMEOUT):
    return _request(cfg, "GET", path, params=params, timeout=timeout)


def post(cfg: dict, path: str, body: dict, timeout: float = DEFAULT_TIMEOUT):
    return _request(cfg, "POST", path, body=body, timeout=timeout)


def delete(cfg: dict, path: str, timeout: float = DEFAULT_TIMEOUT):
    return _request(cfg, "DELETE", path, timeout=timeout)


def delete_json(cfg: dict, path: str, body: dict, timeout: float = DEFAULT_TIMEOUT):
    """DELETE com corpo JSON (rotas de linha/tabela da aba Banco)."""
    return _request(cfg, "DELETE", path, body=body, timeout=timeout)


def get_bytes(cfg: dict, path: str, params: dict | None = None, timeout: float = 120.0) -> bytes:
    """GET devolvendo o corpo cru (exportações CSV)."""
    return _request(cfg, "GET", path, params=params, timeout=timeout, raw_bytes=True)
