"""Endpoints JSON — consumidos pelo Chart.js (fetch) e pelo preview do formulário."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from . import config_store, hub, netspeed, series, status
from .i18n import gettext as _
from .views import admin_required

api = Blueprint("api", __name__)


@api.get("/status")
def all_status():
    cfg = current_app.store.load()
    pause = hub.pause_state(cfg)
    return jsonify([status.station_status(cfg, s, pause) for s in cfg["stations"]])


@api.get("/netspeed")
def net_speed():
    """Taxa das interfaces de rede DO SERVIDOR (contadores do kernel, sem gerar
    tráfego). Aberto como o /api/status — é a mesma página de visualização."""
    return jsonify(netspeed.snapshot())


@api.get("/stations/<station_id>/files/<file_id>/series")
def file_series(station_id: str, file_id: str):
    cfg = current_app.store.load()
    station = config_store.get_station(cfg, station_id)
    if not station:
        return jsonify({"error": _("Estação '%(id)s' não encontrada.", id=station_id)}), 404
    fcfg = config_store.get_file(station, file_id)
    if not fcfg:
        return jsonify({"error": _("Arquivo '%(id)s' não encontrado.", id=file_id)}), 404
    try:
        return jsonify(series.build_series(cfg, station, fcfg))
    except Exception as exc:  # noqa: BLE001 — erro de parse vira mensagem, não 500
        return jsonify({"error": _("Falha ao ler o arquivo: %(erro)s", erro=exc)}), 500


@api.get("/stations/<station_id>/browse")
@admin_required
def station_browse(station_id: str):
    """Navegador de arquivos da estação (uma pasta por vez, como o da aba Coleta) —
    alimenta o card 'Arquivos desta estação no servidor' da tela de edição. Cada
    arquivo vem com ``monitored``/``suggested_alias`` para a marcação."""
    cfg = current_app.store.load()
    station = config_store.get_station(cfg, station_id)
    if not station:
        return jsonify({"error": _("Estação '%(id)s' não encontrada.", id=station_id)}), 404
    try:
        return jsonify(config_store.browse_station_dir(
            cfg, station, request.args.get("sub") or ""))
    except Exception as exc:  # noqa: BLE001 — erro de leitura vira mensagem, não 500
        return jsonify({"error": _("Falha ao listar a pasta: %(erro)s", erro=exc)}), 500


@api.post("/preview")
@admin_required
def preview():
    cfg = current_app.store.load()
    # O preview existe para o usuário VER como o arquivo se divide em colunas ANTES de
    # mapear as variáveis — então não exigimos config completa. Só o caminho é
    # indispensável (sem ele não há o que ler); os demais avisos de validação viram
    # diagnósticos ao lado da tabela, sem bloquear a exibição.
    entry, errors = config_store.validate_file_entry(request.form, require_variables=False)
    if not (entry.get("path") or "").strip():
        return jsonify({"error": _("Informe o caminho do arquivo para pré-visualizar.")}), 400
    try:
        result = series.build_preview(cfg, entry)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": _("Falha ao ler o arquivo: %(erro)s", erro=exc)}), 500
    if errors and "diagnostics" in result:
        result["diagnostics"] = list(errors) + result["diagnostics"]
    return jsonify(result)
