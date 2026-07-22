"""Check de faixa válida: sinaliza medições fora dos limites físicos da variável.

Options (config.yaml):
    flag: 2                      # valor gravado em quality (default 2)
    variables:                   # variable_id -> limites físicos plausíveis
      air_temp:     {min: -10, max: 50}
      rel_humidity: {min: 0,   max: 100}

Valores NULL e variáveis sem limites configurados não são avaliados.
"""
from __future__ import annotations

from .base import Check, register


@register("range_check")
class RangeCheck(Check):
    default_flag = 2

    def violates(self, measurement) -> bool:
        if measurement.value is None:
            return False
        limits = self.options.get("variables", {}).get(measurement.variable_id)
        if limits is None:
            return False
        return not (limits["min"] <= measurement.value <= limits["max"])
