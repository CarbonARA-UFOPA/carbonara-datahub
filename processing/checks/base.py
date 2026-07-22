"""Interface de checks de qualidade e registry plugável.

Cada regra de QA/QC é um Check, aplicado a cada medição JÁ PARSEADA, **em memória,
antes do insert** no banco (ver ``processing/pipeline.py`` — a ingestão chama o
pipeline entre o parse e o INSERT). Adicionar uma regra nova é criar uma subclasse de
``Check``, decorá-la com ``@register("nome")`` e referenciá-la no ``config.yaml`` do
processamento. Nem o pipeline nem a ingestão precisam mudar.

Convenção de flags na coluna ``measurements.quality``:
    0  = ok
    1  = valor inválido no parse (atribuído pelo PARSER, ex.: não numérico)
    2  = fora da faixa física válida (range_check)
    3+ = reservado para regras futuras (spike, sensor travado, calibração, ...)

O pipeline nunca sobrescreve um flag já atribuído pelo parse (quality != 0) e o
primeiro check violado decide o flag — ordene os checks no config do mais grave
para o menos grave.
"""
from __future__ import annotations

from typing import Callable


class Check:
    """Contrato dos checks. ``options`` vem do bloco do check no config.yaml.

    ``flag`` é o valor gravado em ``quality`` quando a regra é violada — vem de
    ``options.flag``, com default em ``default_flag`` da subclasse.
    """

    default_flag = 0

    def __init__(self, options: dict | None = None) -> None:
        self.options = options or {}
        self.flag = int(self.options.get("flag", self.default_flag))

    def violates(self, measurement) -> bool:
        """True se a medição viola a regra (recebe ``self.flag`` em quality).

        Recebe um objeto com ``time``, ``station_id``, ``variable_id``, ``value`` e
        ``quality`` (a ``Measurement`` dos parsers da ingestão). Deve ser barato —
        roda para CADA medição de CADA arquivo — e nunca alterar a medição.
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
