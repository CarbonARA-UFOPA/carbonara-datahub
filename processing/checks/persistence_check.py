"""Check de VALOR TRAVADO (persistence): sinaliza quando a mesma série repete
exatamente o mesmo valor por ``n`` leituras consecutivas — o sintoma clássico de sensor
congelado, cabo solto ou datalogger repetindo a última leitura válida.

Options:
    flag: 4                      # valor gravado em quality (default 4)
    variables:
      rel_humidity: {n: 12}      # 12 leituras iguais seguidas (6 h a cada 30 min)

A partir da n-ésima leitura igual, todas as seguintes são sinalizadas até o valor
mudar. Uma série que legitimamente fica constante (chuva 0.0 mm) NÃO deve ter esta
regra — por isso ela é opcional e por variável.
"""
from __future__ import annotations

from .base import Check, register


@register("persistence_check")
class PersistenceCheck(Check):
    default_flag = 4

    def __init__(self, options: dict | None = None) -> None:
        super().__init__(options)
        self._run: dict[tuple[str, str], tuple[float, int]] = {}   # (último valor, repetições)

    def violates(self, measurement) -> bool:
        if measurement.value is None:
            return False
        opts = self.options.get("variables", {}).get(measurement.variable_id)
        if not opts or not opts.get("n"):
            return False
        key = (measurement.station_id, measurement.variable_id)
        last, count = self._run.get(key, (None, 0))
        count = count + 1 if last == measurement.value else 1
        self._run[key] = (measurement.value, count)
        return count >= int(opts["n"])
