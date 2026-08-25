"""Velocidade das interfaces de rede do servidor (Linux, sem dependências).

Mede a taxa REAL das interfaces do host lendo os contadores do kernel em
``/proc/net/dev`` entre duas leituras — não há teste de banda nem tráfego gerado: é a
contagem do que já passou. No hub, o ``rx`` da interface principal é essencialmente o
que as estações estão enviando.

Qual é a "interface que tem internet": a que carrega a ROTA PADRÃO em
``/proc/net/route`` (destino 00000000). Um servidor com várias placas (rede interna,
Wi-Fi, VPN) tem só uma default — as demais aparecem na lista, marcadas como secundárias.

Fora do Linux (sem ``/proc``) devolve vazio, e a interface some da tela: um mostrador
de rede jamais pode derrubar a página de estações.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

_PROC_DEV = Path("/proc/net/dev")
_PROC_ROUTE = Path("/proc/net/route")
_MIN_INTERVAL = 1.0      # abaixo disto a taxa vira ruído de arredondamento

_lock = threading.Lock()
_last: dict = {"t": None, "counters": {}}
_rates: dict = {}        # iface -> (rx_bps, tx_bps) da última janela válida


def default_iface() -> str | None:
    """Interface da rota padrão (a que tem saída para a internet)."""
    try:
        linhas = _PROC_ROUTE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for linha in linhas[1:]:
        campos = linha.split()
        # Iface Destination Gateway Flags RefCnt Use Metric Mask …
        if len(campos) >= 3 and campos[1] == "00000000":
            return campos[0]
    return None


def _read_dev() -> dict[str, tuple[int, int]]:
    """{interface: (bytes recebidos, bytes enviados)} — acumulados desde o boot."""
    try:
        linhas = _PROC_DEV.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    out: dict[str, tuple[int, int]] = {}
    for linha in linhas[2:]:                       # duas linhas de cabeçalho
        nome, _, resto = linha.partition(":")
        nome = nome.strip()
        if not nome or nome == "lo":               # loopback não é conexão
            continue
        campos = resto.split()
        if len(campos) < 9:
            continue
        try:
            out[nome] = (int(campos[0]), int(campos[8]))
        except ValueError:
            continue
    return out


def snapshot() -> dict:
    """Taxa atual por interface. Chamadas seguidas fecham a janela de medição: a
    primeira só registra os contadores (``medindo``), a partir da segunda há taxa."""
    agora = time.monotonic()
    contadores = _read_dev()
    if not contadores:
        return {"available": False, "primary": None, "interfaces": []}

    with _lock:
        anterior_t, anterior = _last["t"], _last["counters"]
        if anterior_t is None:
            _last.update(t=agora, counters=contadores)
        elif agora - anterior_t >= _MIN_INTERVAL:
            dt = agora - anterior_t
            novas = {}
            for nome, (rx, tx) in contadores.items():
                prev = anterior.get(nome)
                # Contador que ANDOU PARA TRÁS = interface reiniciada (ou 32→64 bits):
                # descartar a janela é melhor que exibir um pico absurdo.
                if prev and rx >= prev[0] and tx >= prev[1]:
                    novas[nome] = ((rx - prev[0]) / dt, (tx - prev[1]) / dt)
            _last.update(t=agora, counters=contadores)
            _rates.clear()
            _rates.update(novas)
        taxas = dict(_rates)

    principal = default_iface()
    ifaces = []
    for nome in sorted(contadores):
        rx_bps, tx_bps = taxas.get(nome, (None, None))
        ifaces.append({
            "name": nome,
            "primary": nome == principal,
            "rx_bps": rx_bps,
            "tx_bps": tx_bps,
            "rx_total": contadores[nome][0],
            "tx_total": contadores[nome][1],
        })
    # A principal primeiro; depois as que têm tráfego; o resto por nome.
    ifaces.sort(key=lambda i: (not i["primary"], -(i["rx_bps"] or 0), i["name"]))
    return {"available": True, "primary": principal, "interfaces": ifaces,
            "measuring": not taxas}
