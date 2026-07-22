"""Check de faixa válida: sinaliza medições fora dos limites físicos da variável.

Options (config.yaml):
    flag: 2                      # valor gravado em quality (default 2)
    variables:                   # variable_id -> limites físicos plausíveis
      air_temp:     {min: -10, max: 50}
      rel_humidity: {min: 0,   max: 100}

Valores NULL não são avaliados (já sinalizados pelo parse com quality=1 quando for o
caso). A atualização é set-based: um UPDATE por variável, só sobre linhas novas
(``ingested_at > since``) e ainda não avaliadas (``quality = 0``).
"""
from __future__ import annotations

from datetime import datetime

from .base import Check, register


@register("range_check")
class RangeCheck(Check):
    def run(self, conn, since: datetime) -> int:
        flag = int(self.options.get("flag", 2))
        variables: dict = self.options.get("variables", {})
        if not variables:
            raise ValueError("range_check sem 'variables' no config — nada a avaliar")

        flagged = 0
        with conn.cursor() as cur:
            for variable_id, limits in variables.items():
                cur.execute(
                    """
                    UPDATE measurements
                       SET quality = %s
                     WHERE variable_id = %s
                       AND ingested_at > %s
                       AND quality = 0
                       AND value IS NOT NULL
                       AND (value < %s OR value > %s)
                    """,
                    (flag, variable_id, since, limits["min"], limits["max"]),
                )
                flagged += cur.rowcount
        return flagged
