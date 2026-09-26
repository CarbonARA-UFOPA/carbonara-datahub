"""Check de SALTO (spike): sinaliza uma medição que varia mais que ``max_delta`` em
relação à medição anterior da MESMA série (estação + variável).

Options (config.yaml ou limites do registro na dashboard):
    flag: 3                      # valor gravado em quality (default 3)
    variables:                   # variable_id -> salto máximo aceitável entre leituras
      air_temp: {max_delta: 5}   # °C entre uma leitura e a seguinte

Guarda a última leitura por série; funciona porque a ingestão lê cada arquivo em ordem
cronológica. A referência avança SEMPRE (mesmo quando a leitura foi sinalizada): um
degrau real marca uma leitura e segue, em vez de marcar tudo para sempre. Valores NULL
não são avaliados nem viram referência.
"""
from __future__ import annotations

from .base import Check, register


@register("spike_check")
class SpikeCheck(Check):
    default_flag = 3

    def __init__(self, options: dict | None = None) -> None:
        super().__init__(options)
        self._last: dict[tuple[str, str], float] = {}

    def violates(self, measurement) -> bool:
        if measurement.value is None:
            return False
        opts = self.options.get("variables", {}).get(measurement.variable_id)
        if not opts or opts.get("max_delta") is None:
            return False
        key = (measurement.station_id, measurement.variable_id)
        prev = self._last.get(key)
        self._last[key] = measurement.value
        if prev is None:
            return False
        return abs(measurement.value - prev) > float(opts["max_delta"])
