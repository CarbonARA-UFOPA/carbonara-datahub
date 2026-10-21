"""Bilinguismo da dashboard (pt-BR ⇄ inglês), sem dependência nova.

Modelo gettext simplificado: o TEXTO EM PORTUGUÊS é a chave. `_("Estações")` devolve
"Stations" quando o idioma ativo é inglês e o próprio texto quando é português — ou
quando falta tradução (nunca quebra: na pior hipótese a frase aparece em português).

- Catálogos: um módulo por área em `app/i18n_en/*.py`, cada um com um dict `EN`
  {texto pt: texto en}; todos são fundidos aqui (chaves repetidas entre módulos são
  aceitas — a última vence). As chaves são normalizadas por espaço (`_norm`), então um
  parágrafo quebrado em várias linhas no template casa com a chave em uma linha só.
- Interpolação: `%(nome)s` no texto + kwargs — `_("Estação %(id)s removida.", id=sid)`.
  A tradução precisa manter os MESMOS `%(nome)s`.
- Idioma ativo: cookie `lang` (`/lang/<código>`, botão no topo), senão
  `server.language` do config.yaml, senão português.
- Templates: o `_` do Jinja devolve `Markup` — o texto literal é do código (confiável,
  pode ter <strong>/<code>), e os kwargs são ESCAPADOS pelo `%` do Markup.
- JS: `/i18n.js` define `window.LANG`, `window.I18N` (catálogo inteiro, só em inglês)
  e `window.t(texto, vars)` — carregado no <head>, antes de qualquer script inline.
"""
from __future__ import annotations

import importlib
import pkgutil
import re
from functools import lru_cache

from markupsafe import Markup, escape

LANGS = ("pt", "en")
DEFAULT_LANG = "pt"
COOKIE = "lang"
LANG_LABELS = {"pt": "Português", "en": "English"}

_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS.sub(" ", s).strip()


@lru_cache(maxsize=1)
def catalog_en() -> dict[str, str]:
    from . import i18n_en

    merged: dict[str, str] = {}
    for mod in sorted(pkgutil.iter_modules(i18n_en.__path__), key=lambda m: m.name):
        table = getattr(importlib.import_module(f"{i18n_en.__name__}.{mod.name}"), "EN", {})
        for pt, en in table.items():
            if pt and en:
                merged[_norm(pt)] = _norm(en) if "\n" not in en.strip() else en.strip()
    return merged


def get_lang() -> str:
    """Idioma da requisição atual; fora de requisição (threads, CLI) = português."""
    from flask import current_app, has_request_context, request

    if not has_request_context():
        return DEFAULT_LANG
    lang = request.cookies.get(COOKIE)
    if lang in LANGS:
        return lang
    try:
        lang = (current_app.store.load().get("server") or {}).get("language")
    except Exception:  # noqa: BLE001 — idioma nunca derruba a página
        lang = None
    return lang if lang in LANGS else DEFAULT_LANG


def translate(text, lang: str | None = None) -> str:
    """Só a busca no catálogo (sem interpolação). Não-string passa direto."""
    if not isinstance(text, str) or not text:
        return text
    lang = lang or get_lang()
    if lang == "pt":
        return text
    return catalog_en().get(_norm(text), text)


def gettext(text, **kwargs) -> str:
    """Para código Python (flash, mensagens de erro): texto puro."""
    out = translate(text)
    if kwargs and isinstance(out, str):
        try:
            out = out % kwargs
        except (KeyError, ValueError, TypeError):
            out = text % kwargs
    return out


_ = gettext


def template_gettext(text, **kwargs):
    """Para os templates: Markup só para texto do CATÁLOGO (escrito no código, pode
    ter <strong>/<code>), kwargs escapados. `_(variável)` com texto que não está no
    catálogo (nome de arquivo, mensagem de exceção…) é ESCAPADO — senão um nome de
    arquivo com HTML viraria marcação na página."""
    if not isinstance(text, str) or not text:
        return text
    if _norm(text) not in catalog_en():
        return escape(text % kwargs if kwargs else text)
    out = translate(text)
    m = Markup(out)
    if kwargs:
        try:
            m = m % kwargs
        except (KeyError, ValueError, TypeError):
            m = Markup(text) % kwargs
    return m


def safe_next(target: str | None) -> str:
    """Destino local para o redirect do seletor de idioma — nunca uma URL externa."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


__all__ = ["LANGS", "LANG_LABELS", "COOKIE", "get_lang", "gettext", "_",
           "template_gettext", "translate", "catalog_en", "safe_next", "escape"]
