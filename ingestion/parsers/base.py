"""Interface de parsers e registry plugável.

Cada formato de arquivo de estação tem um Parser. Adicionar um formato novo é criar
uma subclasse de ``Parser``, decorá-la com ``@register("nome")`` e referenciá-la no
``config.yaml`` da ingestão. O loop de ingestão (``ingest.py``) não precisa mudar.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable


@dataclass(frozen=True)
class Measurement:
    """Uma medição normalizada, pronta para virar uma linha em ``measurements``."""
    time: datetime          # timestamp com timezone
    station_id: str
    variable_id: str
    value: float | None
    quality: int = 0


class Parser:
    """Contrato dos parsers. ``options`` vem do bloco do parser no config.yaml."""

    def __init__(self, options: dict | None = None) -> None:
        self.options = options or {}

    def parse(self, path: str, station_id: str) -> Iterable[Measurement]:
        """Lê ``path`` e devolve um iterável de :class:`Measurement`.

        Deve lançar exceção em arquivo inválido — o arquivo vai para ``quarantine/``.
        """
        raise NotImplementedError


# --- Registry -------------------------------------------------------------------
_REGISTRY: dict[str, type[Parser]] = {}


def register(name: str) -> Callable[[type[Parser]], type[Parser]]:
    """Decorator que registra uma classe de parser sob ``name``."""
    def _decorator(cls: type[Parser]) -> type[Parser]:
        _REGISTRY[name] = cls
        return cls
    return _decorator


def get_parser(name: str, options: dict | None = None) -> Parser:
    """Instancia o parser registrado como ``name``."""
    if name not in _REGISTRY:
        raise KeyError(
            f"parser '{name}' não registrado. Disponíveis: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name](options)
