"""Registry de checks de QA/QC.

IMPORTANTE: importar o módulo do check aqui é o que dispara o ``@register`` — um check
não importado é invisível para o registry (mesma regra dos parsers da ingestão).
"""
from .base import Check, get_check, register  # noqa: F401

from . import persistence_check, range_check, spike_check  # noqa: F401
