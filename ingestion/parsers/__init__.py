"""Pacote de parsers. Importar aqui garante que os parsers se registrem no registry."""
from . import csv_generic  # noqa: F401  (efeito colateral: @register)
from .base import Measurement, Parser, get_parser, register  # noqa: F401
