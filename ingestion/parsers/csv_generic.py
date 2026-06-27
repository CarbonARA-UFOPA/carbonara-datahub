"""Parser CSV genérico, dirigido por configuração.

Espera um CSV "wide": uma coluna de timestamp e uma coluna por variável. O mapeamento
coluna→variable_id e o formato do timestamp vêm das ``options`` (config.yaml).

Exemplo de options::

    timestamp_column: "TIMESTAMP"
    timestamp_format: "%Y-%m-%d %H:%M:%S"   # omita p/ ISO 8601 automático
    timezone: "America/Santarem"            # aplicado se o timestamp for "naive"
    delimiter: ","
    columns:                                # coluna do CSV -> variable_id no banco
      AirTC_Avg: air_temp
      RH: rel_humidity
      Rain_mm_Tot: rainfall
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

from .base import Measurement, Parser, register


@register("csv_generic")
class CsvGenericParser(Parser):
    def parse(self, path: str, station_id: str) -> Iterable[Measurement]:
        ts_col = self.options.get("timestamp_column", "timestamp")
        ts_fmt = self.options.get("timestamp_format")           # None = ISO 8601
        delimiter = self.options.get("delimiter", ",")
        col_map: dict[str, str] = self.options.get("columns", {})
        tzname = self.options.get("timezone")
        tzinfo = ZoneInfo(tzname) if tzname else timezone.utc

        if not col_map:
            raise ValueError("csv_generic exige 'columns' (mapa coluna->variable_id)")

        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh, delimiter=delimiter)
            if ts_col not in (reader.fieldnames or []):
                raise ValueError(
                    f"coluna de timestamp '{ts_col}' ausente em {path}; "
                    f"colunas: {reader.fieldnames}"
                )

            for lineno, row in enumerate(reader, start=2):  # linha 1 = cabeçalho
                ts = self._parse_timestamp(row[ts_col], ts_fmt, tzinfo, path, lineno)
                for csv_col, variable_id in col_map.items():
                    raw = (row.get(csv_col) or "").strip()
                    if raw == "":
                        continue                            # célula vazia: pula
                    value = self._to_float(raw)
                    yield Measurement(
                        time=ts,
                        station_id=station_id,
                        variable_id=variable_id,
                        value=value,
                        quality=0 if value is not None else 1,  # 1 = valor não numérico
                    )

    @staticmethod
    def _parse_timestamp(raw, fmt, tzinfo, path, lineno) -> datetime:
        raw = (raw or "").strip()
        try:
            dt = datetime.strptime(raw, fmt) if fmt else datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(f"timestamp inválido '{raw}' em {path}:{lineno}") from exc
        # Aplica timezone se o valor for "naive" (sem offset).
        return dt.replace(tzinfo=tzinfo) if dt.tzinfo is None else dt

    @staticmethod
    def _to_float(raw: str) -> float | None:
        try:
            return float(raw)
        except ValueError:
            return None    # sentinelas (NAN, -9999, "NA"...) viram NULL com quality=1
