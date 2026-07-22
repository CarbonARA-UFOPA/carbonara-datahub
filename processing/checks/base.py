"""Interface de checks de qualidade e registry plugável.

Cada regra de QA/QC é um Check. Adicionar uma regra nova é criar uma subclasse de
``Check``, decorá-la com ``@register("nome")`` e referenciá-la no ``config.yaml`` do
processamento. O loop (``process.py``) não precisa mudar.

Convenção de flags na coluna ``measurements.quality``:
    0  = ok / ainda não avaliado
    1  = valor inválido no parse (atribuído pela INGESTÃO, ex.: não numérico)
    2  = fora da faixa física válida (range_check)
    3+ = reservado para regras futuras (spike, sensor travado, calibração, ...)

Um check só deve sinalizar linhas com ``quality = 0`` — nunca sobrescrever um flag
já atribuído por outra etapa.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable


class Check:
    """Contrato dos checks. ``options`` vem do bloco do check no config.yaml."""

    def __init__(self, options: dict | None = None) -> None:
        self.options = options or {}

    def run(self, conn, since: datetime) -> int:
        """Avalia as medições com ``ingested_at > since`` e retorna quantas sinalizou.

        Deve operar em SQL (set-based) sempre que possível e atualizar apenas linhas
        com ``quality = 0``. Exceções são capturadas pelo loop: a passada do check é
        desfeita (rollback) e o serviço segue vivo.
        """
        raise NotImplementedError


# --- Registry -------------------------------------------------------------------
_REGISTRY: dict[str, type[Check]] = {}


def register(name: str) -> Callable[[type[Check]], type[Check]]:
    """Decorator que registra uma classe de check sob ``name``."""
    def _decorator(cls: type[Check]) -> type[Check]:
        _REGISTRY[name] = cls
        return cls
    return _decorator


def get_check(name: str, options: dict | None = None) -> Check:
    """Instancia o check registrado como ``name``."""
    if name not in _REGISTRY:
        raise KeyError(
            f"check '{name}' não registrado. Disponíveis: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name](options)
