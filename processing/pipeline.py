"""Pipeline de QA/QC do CARBONARA — roda ANTES do insert, em memória.

A ingestão monta o pipeline uma vez (a partir do ``processing/config.yaml``) e chama
``evaluate(measurement)`` para cada medição parseada; o inteiro retornado é gravado
direto na coluna ``measurements.quality``. Sem config (ou sem ``checks:`` nele), o
pipeline fica vazio e tudo passa com o flag que veio do parse.

Regras:
- um flag do parse (quality != 0, ex.: valor não numérico) nunca é sobrescrito;
- o PRIMEIRO check violado decide o flag (ordene no config do mais grave ao menos);
- os checks nunca alteram a medição — o valor cru sempre entra no banco, auditável.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from . import checks


class QualityPipeline:
    def __init__(self, check_list: list | None = None) -> None:
        self.checks = list(check_list or [])

    @classmethod
    def from_file(cls, path: str) -> "QualityPipeline":
        """Monta o pipeline do config YAML; arquivo ausente = pipeline vazio (QC off)."""
        p = Path(path)
        if not p.is_file():
            return cls([])
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return cls([
            checks.get_check(spec["check"], spec.get("options", {}))
            for spec in cfg.get("checks", [])
        ])

    def evaluate(self, measurement) -> int:
        """Flag de qualidade da medição (0 = aprovada)."""
        if measurement.quality != 0:
            return measurement.quality  # flag do parse tem precedência
        for check in self.checks:
            if check.violates(measurement):
                return check.flag
        return 0
