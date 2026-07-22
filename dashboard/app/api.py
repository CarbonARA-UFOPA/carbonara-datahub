"""Endpoints JSON — consumidos pelo Chart.js (fetch) e pelo preview do formulário."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from . import config_store, series, status

api = Blueprint("api", __name__)


@api.get("/status")
def all_status():
    cfg = current_app.store.load()
    return jsonify([status.station_status(cfg, s) for s in cfg["stations"]])


@api.get("/stations/<station_id>/files/<file_id>/series")
def file_series(station_id: str, file_id: str):
    cfg = current_app.store.load()
    station = config_store.get_station(cfg, station_id)
    if not station:
        return jsonify({"error": f"Estação '{station_id}' não encontrada."}), 404
    fcfg = config_store.get_file(station, file_id)
    if not fcfg:
        return jsonify({"error": f"Arquivo '{file_id}' não encontrado."}), 404
    try:
        return jsonify(series.build_series(cfg, station, fcfg))
    except Exception as exc:  # noqa: BLE001 — erro de parse vira mensagem, não 500
        return jsonify({"error": f"Falha ao ler o arquivo: {exc}"}), 500


@api.post("/preview")
def preview():
    cfg = current_app.store.load()
    entry, errors = config_store.validate_file_entry(request.form)
    if errors:
        return jsonify({"error": " ".join(errors)}), 400
    try:
        return jsonify(series.build_preview(cfg, entry))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Falha ao ler o arquivo: {exc}"}), 500
