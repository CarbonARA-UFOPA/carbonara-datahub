"""Endpoints JSON — consumidos pelo Chart.js (fetch) e pelo preview do formulário."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from . import config_store, series, status
from .views import admin_required

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


@api.get("/stations/<station_id>/dat-files")
@admin_required
def dat_files(station_id: str):
    """Lista os .dat de incoming/<id>/ (recursivo) para a tela de descoberta.
    Só é chamado sob demanda (clique no botão) — nunca no carregamento da página."""
    cfg = current_app.store.load()
    station = config_store.get_station(cfg, station_id)
    if not station:
        return jsonify({"error": f"Estação '{station_id}' não encontrada."}), 404
    try:
        base = config_store.resolve_data_path(cfg, f"incoming/{station_id}")
        files = config_store.discover_dat_files(cfg, station)
    except Exception as exc:  # noqa: BLE001 — erro de varredura vira mensagem, não 500
        return jsonify({"error": f"Falha ao varrer a pasta: {exc}"}), 500
    return jsonify({"base": str(base), "files": files})


@api.post("/preview")
@admin_required
def preview():
    cfg = current_app.store.load()
    entry, errors = config_store.validate_file_entry(request.form)
    if errors:
        return jsonify({"error": " ".join(errors)}), 400
    try:
        return jsonify(series.build_preview(cfg, entry))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Falha ao ler o arquivo: {exc}"}), 500
