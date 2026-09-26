"""Compatibilidade: o parse dos arquivos vive em ``carbonara_parse.tailer`` (pacote
compartilhado com a ingestão — uma implementação só, para que o preview e o banco
nunca divirjam). Este módulo só reexporta."""
from carbonara_parse.tailer import (  # noqa: F401
    COLUMN_TYPES, head_lines, parse_rows, tail_data_lines, tail_lines, tail_lines_since,
    to_bool, to_float, to_int,
)
