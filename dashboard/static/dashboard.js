/* Dashboard CARBONARA — gráficos (Chart.js) e formulário de arquivo (preview). */
"use strict";

const PALETTE = ["#1f6f4a", "#3568b0", "#d64545", "#8a5cd6", "#c98a1f",
                 "#2a9d8f", "#b03568", "#6b7a72"];
const LIMIT_COLOR = "#d64545";

/* Plugin: linhas tracejadas nos limites físicos (mín/máx) da variável. */
const limitLinesPlugin = {
  id: "limitLines",
  afterDatasetsDraw(chart) {
    const lim = chart.$limits;
    if (!lim || (lim.min == null && lim.max == null)) return;
    const y = chart.scales.y, area = chart.chartArea, ctx = chart.ctx;
    [["máx", lim.max, -4], ["mín", lim.min, 11]].forEach(([name, val, dy]) => {
      if (val == null) return;
      const py = y.getPixelForValue(val);
      if (py < area.top - 1 || py > area.bottom + 1) return;
      ctx.save();
      ctx.strokeStyle = LIMIT_COLOR;
      ctx.setLineDash([6, 4]);
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(area.left, py); ctx.lineTo(area.right, py); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = LIMIT_COLOR;
      ctx.font = "10px sans-serif";
      ctx.fillText(`${name} ${val}`, area.left + 4, py + dy);
      ctx.restore();
    });
  },
};

function outOfLimits(lim, v) {
  if (!lim || v == null) return false;
  return (lim.min != null && v < lim.min) || (lim.max != null && v > lim.max);
}

/* Cria/atualiza um gráfico de linha com limites físicos (marcação + trechos vermelhos). */
/* Formatação por TIPO da coluna (numero/inteiro/booleano/epoch/texto). O valor
   plotado é sempre numérico — o tipo muda como ele é LIDO na dica e no eixo Y:
   um epoch vira hora, um booleano vira sim/não, um inteiro perde as casas decimais. */
function fmtByType(v, value, curto) {
  if (value == null || !Number.isFinite(value)) return "—";
  const p = (n, w) => String(n).padStart(w || 2, "0");
  switch (v.type) {
    case "epoch": {
      const d = new Date(value * 1000);
      let t = `${p(d.getHours())}:${p(d.getMinutes())}`;
      if (v.ts_res === "s" || v.ts_res === "ms") t += `:${p(d.getSeconds())}`;
      if (v.ts_res === "ms") t += `.${p(d.getMilliseconds(), 3)}`;
      return curto ? t : `${p(d.getDate())}/${p(d.getMonth() + 1)}/${d.getFullYear()} ${t}`;
    }
    case "booleano": return value >= 0.5 ? "sim" : "não";
    case "inteiro": return Math.round(value).toLocaleString("pt-BR");
    default: return (Math.round(value * 100) / 100).toLocaleString("pt-BR");
  }
}

function makeOrUpdateChart(canvas, labels, v, color, opts = {}) {
  const label = v.unit ? `${v.label} (${v.unit})` : v.label;
  const limits = { min: v.min ?? null, max: v.max ?? null };
  if (canvas._chart) {
    const ch = canvas._chart;
    ch.$limits = limits;
    ch.$var = v;
    ch.data.labels = labels;
    ch.data.datasets[0].data = v.values;
    ch.data.datasets[0].label = label;
    ch.options.scales.y.suggestedMin = limits.min ?? undefined;
    ch.options.scales.y.suggestedMax = limits.max ?? undefined;
    ch.update("none");
    return;
  }
  const chart = new Chart(canvas, {
    type: "line",
    plugins: [limitLinesPlugin],
    data: {
      labels,
      datasets: [{
        label, data: v.values, borderColor: color, backgroundColor: color + "22",
        borderWidth: 1.5, pointRadius: 0, spanGaps: false, fill: true, tension: 0.15,
        // trechos fora dos limites físicos ficam vermelhos (dado ruim à primeira vista).
        // Fallback para o closure `limits`: no primeiro render (dentro do construtor)
        // canvas._chart ainda não foi atribuído.
        segment: {
          borderColor: (s) => {
            const lim = (canvas._chart && canvas._chart.$limits) || limits;
            return outOfLimits(lim, s.p1.parsed.y) || outOfLimits(lim, s.p0.parsed.y)
              ? LIMIT_COLOR : undefined;
          },
        },
      }],
    },
    options: {
      animation: false, responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: !!opts.legend },
        tooltip: {
          callbacks: {
            label: (c) => {
              const varc = (canvas._chart && canvas._chart.$var) || v;
              const un = varc.unit && varc.type !== "epoch" && varc.type !== "booleano"
                ? ` ${varc.unit}` : "";
              return `${varc.label}: ${fmtByType(varc, c.parsed.y)}${un}`;
            },
          },
        },
      },
      scales: {
        x: {
          ticks: { autoSkip: false },
          // Âncora os rótulos do eixo x no ÚLTIMO ponto (a leitura mais RECENTE é o que
          // importa): escolhe ~N ticks espaçados a partir do FIM, garantindo sempre o
          // último timestamp (antes o autoSkip espaçava do primeiro e o último sumia).
          afterBuildTicks: (axis) => {
            const n = (axis.chart.data.labels || []).length;
            if (!n) return;
            const maxT = opts.legend ? 10 : 6;
            const step = Math.max(1, Math.ceil(n / maxT));
            const idx = [];
            for (let i = n - 1; i >= 0; i -= step) idx.unshift(i);
            axis.ticks = idx.map((i) => ({ value: i }));
          },
        },
        y: {
          suggestedMin: v.type === "booleano" ? 0 : (limits.min ?? undefined),
          suggestedMax: v.type === "booleano" ? 1 : (limits.max ?? undefined),
          ticks: {
            /* no eixo, epoch vira só a hora e booleano vira sim/não */
            callback: (val) => {
              const varc = (canvas._chart && canvas._chart.$var) || v;
              if (varc.type === "epoch") return fmtByType(varc, val, true);
              if (varc.type === "booleano") return val >= 0.5 ? "sim" : "não";
              if (varc.type === "inteiro") return Math.round(val).toLocaleString("pt-BR");
              return val;
            },
            stepSize: v.type === "booleano" ? 1 : undefined,
          },
        },
      },
    },
  });
  chart.$limits = limits;
  chart.$var = v;
  canvas._chart = chart;
  if (opts.height) canvas.parentElement.style.height = opts.height;
}

document.addEventListener("DOMContentLoaded", () => {
  initCharts();
  initCompare();
  initFileForm();
  initStationBrowser();
  initMapPage();
});

/* --- Gráficos (página de detalhe da estação) ---------------------------------- */
function initCharts() {
  const blocks = document.querySelectorAll(".file-block[data-series-url]");
  if (!blocks.length) return;
  // Reajusta os gráficos ao reexpandir um arquivo — se foram criados recolhidos
  // (display:none), o Chart.js os desenha com 0px até um resize.
  document.querySelectorAll(".file-charts").forEach((d) => {
    d.addEventListener("toggle", () => {
      if (d.open) d.querySelectorAll("canvas").forEach((c) => c._chart && c._chart.resize());
    });
  });
  blocks.forEach(loadSeries);
  const every = (window.REFRESH_SECONDS || 60) * 1000;
  setInterval(() => blocks.forEach(loadSeries), every);
}

async function loadSeries(block) {
  const errEl = block.querySelector(".series-error");
  const infoEl = block.querySelector(".series-info");
  try {
    const resp = await fetch(block.dataset.seriesUrl);
    const data = await resp.json();
    /* 'pending' = arquivo com data/hora que ainda não chegou: não é erro de
       configuração, então nada de vermelho — some com os gráficos e explica em tom
       neutro. Volta sozinho na próxima atualização, quando o arquivo aparecer. */
    if (data.error) {
      errEl.textContent = data.error;
      errEl.classList.toggle("warn", !data.pending);
      errEl.classList.toggle("meta", !!data.pending);
      block.classList.toggle("series-pending", !!data.pending);
      return;
    }
    errEl.classList.add("warn");
    errEl.classList.remove("meta");
    block.classList.remove("series-pending");
    /* Arquivo REALMENTE lido agora (caminho dinâmico muda de arquivo sozinho; o id e o
       apelido do cadastro ficaram congelados no dia em que ele foi criado). */
    const liveEl = block.querySelector(".live-file");
    if (liveEl) {
      const pi = data.path_info || {};
      const nome = (data.path || "").split("/").pop();
      liveEl.textContent = pi.dynamic && nome
        ? (pi.fallback ? `→ lendo ${nome} (o do momento ainda não chegou)` : `→ lendo ${nome}`)
        : "";
    }
    errEl.textContent = (data.problems || []).join(" · ");
    infoEl.textContent = ` · ${data.rows_read} linhas lidas` +
      (data.decimated ? " (decimado p/ o gráfico)" : "") +
      ` · atualizado ${data.generated_at}`;

    data.variables.forEach((v, i) => {
      if (v.type === "texto") {          /* texto não tem gráfico: só o último valor */
        const box = block.querySelector(`.text-value[data-var="${i}"]`);
        if (box) box.textContent = v.last_text || "—";
        return;
      }
      const canvas = block.querySelector(`canvas[data-var="${i}"]`);
      if (!canvas) return;
      makeOrUpdateChart(canvas, data.timestamps, v, PALETTE[i % PALETTE.length],
                        { legend: true, height: "230px" });
    });
  } catch (e) {
    errEl.textContent = "Falha ao buscar os dados: " + e;
  }
}

/* --- Tela Comparar: TODAS as séries selecionadas num único gráfico -------------- */
/* Cada arquivo tem seus próprios timestamps; o eixo comum é a UNIÃO dos instantes
   (epochs da API), com null onde uma série não tem ponto (spanGaps liga a linha).
   A legenda identifica a origem: estação · arquivo · variável (unidade). */
function initCompare() {
  const cfgEl = document.getElementById("cmp-config");
  const canvas = document.getElementById("cmp-one");
  if (!cfgEl || !canvas) return;
  let items;
  try { items = JSON.parse(cfgEl.textContent || "[]"); } catch (e) { items = []; }
  if (!items.length) return;
  const card = canvas.closest(".cmp-single");
  const errEl = card.querySelector(".series-error");
  const infoEl = card.querySelector(".series-info");

  const refresh = async () => {
    // Um fetch por arquivo, mesmo com várias variáveis dele selecionadas.
    const byUrl = new Map();
    items.forEach((it) => byUrl.set(it.series_url, null));
    await Promise.all([...byUrl.keys()].map(async (url) => {
      try { byUrl.set(url, await (await fetch(url)).json()); }
      catch (e) { byUrl.set(url, { error: "falha ao buscar os dados: " + e }); }
    }));

    const problems = [], series = [];
    let generatedAt = null;
    items.forEach((it) => {
      const src = `${it.sname} · ${it.fid}`;
      const data = byUrl.get(it.series_url);
      if (!data || data.error) { problems.push(`${src}: ${data ? data.error : "sem resposta"}`); return; }
      (data.problems || []).forEach((p) => problems.push(`${src}: ${p}`));
      generatedAt = data.generated_at || generatedAt;
      const v = data.variables[it.idx];
      if (!v) { problems.push(`${src}: variável não encontrada na série`); return; }
      if (!data.epochs) { problems.push(`${src}: recarregue a dashboard no servidor (API sem eixo de tempo comum)`); return; }
      // Alinha no MINUTO (a resolução exibida): dataloggers gravam com segundos
      // diferentes entre si (09:00:15 vs 09:00:47) e, com o epoch exato, o mesmo
      // minuto virava dois instantes — linhas "duplicadas" na tabela, com valor de
      // um lado e "—" do outro. Se uma série tiver mais de um ponto no mesmo
      // minuto, prevalece o último (a ordem do forEach é cronológica).
      const pts = new Map();
      data.epochs.forEach((e, j) => {
        if (e != null) pts.set(Math.floor(e / 60) * 60, v.values[j]);
      });
      if (!pts.size) { problems.push(`${src}: nenhum timestamp reconhecido`); return; }
      const unit = v.unit ? ` (${v.unit})` : "";
      series.push({
        it, v, pts,
        keys: [...pts.keys()].sort((a, b) => a - b),  // p/ busca binária no cursor
        color: PALETTE[it.color % PALETTE.length],
        fullLabel: `${it.sname} · ${it.fid} · ${v.label}${unit}`,
        unit: v.unit || "",
      });
    });
    errEl.textContent = problems.join(" · ");
    if (!series.length) return;

    // Eixo comum: união de todos os instantes, ordenada.
    const all = new Set();
    series.forEach((s) => s.pts.forEach((_, e) => all.add(e)));
    const epochs = [...all].sort((a, b) => a - b);
    const p2 = (n) => String(n).padStart(2, "0");
    const labels = epochs.map((e) => {
      const d = new Date(e * 1000);
      /* mesma convenção do resto da dashboard: data completa, hora e minuto, sem segundos */
      return `${p2(d.getDate())}/${p2(d.getMonth() + 1)}/${d.getFullYear()} ` +
             `${p2(d.getHours())}:${p2(d.getMinutes())}`;
    });
    const datasets = series.map((s) => ({
      label: s.fullLabel,
      data: epochs.map((e) => (s.pts.has(e) ? s.pts.get(e) : null)),
      borderColor: s.color, backgroundColor: s.color + "22",
      borderWidth: 1.5, pointRadius: 0, spanGaps: true, fill: false, tension: 0.15,
    }));
    infoEl.textContent = `${series.length} série(s) · ${epochs.length} instantes no eixo comum` +
      (generatedAt ? ` · atualizado ${generatedAt}` : "");

    canvas.$cmp = { epochs, series };  // lido pelo cursor (linha vertical) e handlers
    renderCmpTable(epochs, series);

    if (canvas._chart) {
      const ch = canvas._chart;
      ch.data.labels = labels;
      ch.data.datasets = datasets;
      ch.update("none");
      return;
    }
    canvas._chart = new Chart(canvas, {
      type: "line",
      plugins: [cmpCrosshairPlugin],
      data: { labels, datasets },
      options: {
        animation: false, responsive: true, maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        // tooltip nativo desligado: a leitura sob o cursor é a caixinha própria
        // (#cmp-tip), que mostra TODAS as séries — inclusive as sem ponto exato
        // no instante (o tooltip nativo omite datasets com null).
        plugins: { legend: { display: true, position: "bottom" }, tooltip: { enabled: false } },
        scales: {
          x: {
            ticks: { autoSkip: false },
            // mesmos ticks ancorados no ÚLTIMO ponto usados nos demais gráficos
            afterBuildTicks: (axis) => {
              const n = (axis.chart.data.labels || []).length;
              if (!n) return;
              const step = Math.max(1, Math.ceil(n / 10));
              const idx = [];
              for (let i = n - 1; i >= 0; i -= step) idx.unshift(i);
              axis.ticks = idx.map((i) => ({ value: i }));
            },
          },
        },
      },
    });
    installCmpCursor(canvas);
  };
  refresh();
  setInterval(refresh, (window.REFRESH_SECONDS || 60) * 1000);
}

/* Formatação compartilhada da tela Comparar */
function cmpFmtNum(v) {
  if (v == null || !Number.isFinite(v)) return "—";
  return (Math.round(v * 100) / 100).toLocaleString("pt-BR");
}
function cmpFmtEpoch(e, withYear) {
  const d = new Date(e * 1000);
  const p = (n) => String(n).padStart(2, "0");
  const dia = `${p(d.getDate())}/${p(d.getMonth() + 1)}${withYear ? "/" + d.getFullYear() : ""}`;
  return `${dia} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
/* Último ponto da série em t OU ANTES (busca binária) — o "valor vigente". */
function cmpLastAtOrBefore(s, t) {
  const k = s.keys;
  let lo = 0, hi = k.length - 1, ans = -1;
  while (lo <= hi) {
    const m = (lo + hi) >> 1;
    if (k[m] <= t) { ans = m; lo = m + 1; } else hi = m - 1;
  }
  return ans < 0 ? null : { e: k[ans], v: s.pts.get(k[ans]) };
}

/* Linha vertical no instante sob o cursor (desligada quando o mouse sai). */
const cmpCrosshairPlugin = {
  id: "cmpCrosshair",
  afterDatasetsDraw(chart) {
    const x = chart.$crossX;
    if (x == null) return;
    const area = chart.chartArea, ctx = chart.ctx;
    if (x < area.left || x > area.right) return;
    ctx.save();
    ctx.strokeStyle = "rgba(40, 60, 50, .45)";
    ctx.setLineDash([5, 3]);
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, area.top); ctx.lineTo(x, area.bottom); ctx.stroke();
    ctx.restore();
  },
};

/* Cursor da tela Comparar: segue o mouse, trava no instante mais próximo do eixo
   comum e mostra a leitura de TODAS as séries naquele momento. Série sem ponto
   exato exibe o último valor anterior, com o horário dele ao lado. */
function installCmpCursor(canvas) {
  const tip = document.getElementById("cmp-tip");
  if (!tip) return;
  const chart = () => canvas._chart;

  canvas.addEventListener("mousemove", (ev) => {
    const ch = chart(), cmp = canvas.$cmp;
    if (!ch || !cmp || !cmp.epochs.length) return;
    const rect = canvas.getBoundingClientRect();
    const px = ev.clientX - rect.left;
    const area = ch.chartArea;
    if (px < area.left || px > area.right) { hideCmpCursor(canvas); return; }
    let idx = Math.round(ch.scales.x.getValueForPixel(px));
    idx = Math.max(0, Math.min(cmp.epochs.length - 1, idx));
    const t = cmp.epochs[idx];

    ch.$crossX = ch.scales.x.getPixelForValue(idx);
    ch.draw();

    let html = `<div class="cmp-tip-title">${esc(cmpFmtEpoch(t, true))}</div>`;
    cmp.series.forEach((s) => {
      const hit = s.pts.has(t) ? { e: t, v: s.pts.get(t) } : cmpLastAtOrBefore(s, t);
      const val = hit ? cmpFmtNum(hit.v) : "—";
      const unit = s.unit ? ` ${esc(s.unit)}` : "";
      const carried = hit && hit.e !== t
        ? ` <span class="carried">(${esc(cmpFmtEpoch(hit.e, false))})</span>` : "";
      html += `<div class="cmp-tip-row">` +
        `<span class="cmp-dot" style="background:${s.color}"></span>` +
        `<span>${esc(s.it.sname)} · ${esc(s.v.label)}</span>` +
        `<span class="val">${val}${unit}${carried}</span></div>`;
    });
    tip.innerHTML = html;
    tip.hidden = false;
    // caixinha do lado oposto ao cursor para não cobrir a linha
    const box = canvas.parentElement.getBoundingClientRect();
    const tw = tip.offsetWidth || 260;
    const naDireita = px + 16 + tw > box.width;
    tip.style.left = naDireita ? `${Math.max(0, px - 16 - tw)}px` : `${px + 16}px`;
  });
  canvas.addEventListener("mouseleave", () => hideCmpCursor(canvas));
}

function hideCmpCursor(canvas) {
  const tip = document.getElementById("cmp-tip");
  if (tip) tip.hidden = true;
  const ch = canvas._chart;
  if (ch && ch.$crossX != null) { ch.$crossX = null; ch.draw(); }
}

/* Tabela "Valores comparados": um instante do eixo comum por linha (recente
   primeiro), uma coluna por série; célula vazia = série sem ponto no instante. */
function renderCmpTable(epochs, series) {
  const table = document.getElementById("cmp-table");
  if (!table) return;
  const note = document.getElementById("cmp-table-note");
  const MAX_ROWS = 200;
  const total = epochs.length;
  const n = Math.min(total, MAX_ROWS);

  let html = "<thead><tr><th>Data/hora</th>";
  series.forEach((s) => {
    html += `<th><span class="cmp-dot" style="background:${s.color}"></span> ` +
            `${esc(s.fullLabel)}</th>`;
  });
  html += "</tr></thead><tbody>";
  for (let i = total - 1; i >= total - n; i--) {
    const t = epochs[i];
    html += `<tr><td><small>${esc(cmpFmtEpoch(t, true))}</small></td>`;
    series.forEach((s) => {
      html += `<td class="num">${s.pts.has(t) ? cmpFmtNum(s.pts.get(t)) : "—"}</td>`;
    });
    html += "</tr>";
  }
  html += "</tbody>";
  table.innerHTML = html;
  if (note) {
    note.textContent = total > n
      ? `— mostrando os ${n} instantes mais recentes de ${total} (mais recente primeiro); ` +
        `“—” = a série não tem ponto naquele instante`
      : "— um instante por linha (mais recente primeiro); “—” = a série não tem ponto naquele instante";
  }
}


/* --- Página Mapa: estações georreferenciadas + gráficos ao clicar --------------- */
const STATUS_LABEL = {
  receiving: "Recebendo dados", online: "Online",
  offline: "Offline", unknown: "Sem sinal", paused: "Recepção pausada",
};

function statusColor(status) {
  const vars = { receiving: "--purple", online: "--green", offline: "--red",
                 paused: "--amber" };
  const name = vars[status] || "--gray";
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#8a938e";
}

function initMapPage() {
  const mapEl = document.getElementById("station-map");
  const dataEl = document.getElementById("map-data");
  if (!mapEl || !dataEl || typeof L === "undefined") return;
  const stations = JSON.parse(dataEl.textContent);

  // Camadas base: padrão (OSM), satélite (Esri World Imagery) e relevo (OpenTopoMap).
  const osm = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  });
  const satelite = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
      maxZoom: 19,
      attribution: "Tiles &copy; Esri — Source: Esri, Maxar, Earthstar Geographics",
    });
  // O servidor do OpenTopoMap é lento e essa é a camada inicial, então pedimos os
  // tiles um nível de zoom ABAIXO e os desenhamos em 512px (tileSize + zoomOffset):
  // 1/4 das requisições por tela — metade da resolução, carregamento bem mais rápido.
  // maxZoom 18 com zoomOffset -1 => zoom de URL 17, o máximo que o OpenTopoMap serve.
  const relevo = L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", {
    maxZoom: 18,
    tileSize: 512,
    zoomOffset: -1,
    keepBuffer: 1,          // menos tiles fora da tela (padrão: 2)
    updateWhenZooming: false, // não busca tiles intermediários durante a animação
    attribution: 'Map data: &copy; OpenStreetMap · SRTM | Style: &copy; <a href="https://opentopomap.org">OpenTopoMap</a>',
  });

  const map = L.map(mapEl, { layers: [relevo] }); // relevo é a camada inicial
  // Controle recolhido (ícone padrão do Leaflet): expande ao passar o mouse.
  L.control.layers(
    { "Relevo": relevo, "Satélite": satelite, "Padrão (OpenStreetMap)": osm },
    null, { collapsed: true }
  ).addTo(map);

  const located = stations.filter((s) => s.latitude != null && s.longitude != null);
  if (!located.length) {
    map.setView([-3.5, -55.0], 6); // Amazônia central como enquadramento padrão
  }

  // Tooltip do marcador: cabeçalho (nome + status) e rodapé (última leitura)
  // sempre visíveis; a lista de valores mostra ~10 linhas e rola para o restante.
  const lastVals = new Map(); // station.id -> { values, ts, at }

  function fmtVal(v) {
    if (v == null || !Number.isFinite(v)) return "—";
    return (Math.round(v * 100) / 100).toLocaleString("pt-BR");
  }

  function markerTooltip(s, cached, loading) {
    const head =
      `<div class="station-tip-head"><strong>${esc(s.name)}</strong>` +
      `<span class="station-tip-status">${STATUS_LABEL[s.status] || esc(s.status)}</span></div>`;
    let mid = "";
    if (cached) mid = `<div class="station-tip-values">${cached.values}</div>`;
    else if (loading) mid = '<div class="station-tip-values"><em>carregando últimos valores…</em></div>';
    const foot = (cached || loading)
      ? `<div class="station-tip-foot">última leitura: <strong>${esc((cached && cached.ts) || "—")}</strong></div>`
      : "";
    return `<div class="station-tip">${head}${mid}${foot}</div>`;
  }

  async function fetchLastValues(s) {
    const lines = [];
    let lastTs = null;
    const results = await Promise.all(s.files.map((f) =>
      fetch(f.series_url).then((r) => r.json()).catch(() => null)));
    results.forEach((data, fi) => {
      if (!data || data.error) return;
      // "última leitura" com ANO (data.last_reading = '%d/%m/%Y %H:%M'); fallback no
      // timestamp do eixo (sem ano) se o backend for antigo.
      if (data.last_reading) lastTs = data.last_reading;
      const rows = [];
      data.variables.forEach((v) => {
        let val = null;
        for (let i = v.values.length - 1; i >= 0; i--) {
          if (v.values[i] != null) {
            val = v.values[i];
            if (!data.last_reading && data.timestamps[i]) lastTs = data.timestamps[i];
            break;
          }
        }
        rows.push(
          `<div class="station-tip-row"><span>${esc(v.label)}</span>` +
          `<strong>${fmtVal(val)}${v.unit ? " " + esc(v.unit) : ""}</strong></div>`);
      });
      // cada arquivo .dat (sensor/equipamento) é um bloco recolhível no balão
      lines.push(
        `<details class="station-tip-group" open>` +
        `<summary class="station-tip-file">${esc(s.files[fi].alias || s.files[fi].id)}` +
        `<span class="station-tip-count">${rows.length}</span></summary>` +
        rows.join("") + `</details>`);
    });
    if (!lines.length) return { values: "<em>sem dados disponíveis</em>", ts: null };
    return { values: lines.join(""), ts: lastTs };
  }

  const markers = new Map();
  located.forEach((s) => {
    const marker = L.circleMarker([s.latitude, s.longitude], {
      radius: 9, weight: 2, color: "#ffffff",
      fillColor: statusColor(s.status), fillOpacity: 0.95,
    }).addTo(map);
    // Tooltip com abertura/fechamento MANUAL (permanent) e uma tolerância de
    // 300 ms no mouseout: cruzar o vão entre o marcador e o balão não o fecha,
    // então dá para alcançar a área de rolagem. Como o tooltip é interactive,
    // o mouse sobre ele conta como mouseover do marcador (cancela o timer).
    marker.bindTooltip(markerTooltip(s, null, false),
                       { permanent: true, opacity: 0.97, interactive: true,
                         direction: "top", offset: [0, -2] });
    marker.closeTooltip();
    let closeTimer = null;
    let valsShown = false;  // o balão aberto já exibe valores?
    let fetching = false;   // busca em voo (evita fetches duplicados)
    marker.on("tooltipopen", (ev) => {
      const el = ev.tooltip.getElement();
      L.DomEvent.disableScrollPropagation(el);
      L.DomEvent.disableClickPropagation(el);
    });
    marker.on("tooltipclose", () => { valsShown = false; });
    marker.on("mouseover", async () => {
      clearTimeout(closeTimer);
      marker.openTooltip();
      const cached = lastVals.get(s.id);
      const fresh = cached && Date.now() - cached.at < (window.REFRESH_SECONDS || 60) * 1000;
      // Entrar no balão re-dispara mouseover (tooltip é interactive): se o conteúdo
      // já está na tela, NÃO reconstruir — senão a rolagem volta ao topo.
      if (valsShown && (fresh || fetching)) return;
      marker.setTooltipContent(markerTooltip(s, cached || null, !cached));
      valsShown = !!cached;
      if (fresh || fetching) return;
      fetching = true;
      try {
        const res = await fetchLastValues(s);
        lastVals.set(s.id, { ...res, at: Date.now() });
        if (marker.isTooltipOpen()) {
          marker.setTooltipContent(markerTooltip(s, lastVals.get(s.id), false));
          valsShown = true;
        }
      } finally {
        fetching = false;
      }
    });
    marker.on("mouseout", () => {
      clearTimeout(closeTimer);
      closeTimer = setTimeout(() => marker.closeTooltip(), 300);
    });
    marker.on("click", (ev) => {
      // clique DENTRO do balão (ex.: recolher um arquivo) não abre a subjanela
      const tip = marker.getTooltip();
      const tipEl = tip && tip.getElement();
      if (tipEl && ev.originalEvent && tipEl.contains(ev.originalEvent.target)) return;
      marker.closeTooltip();
      openMapPanel(s);
    });
    markers.set(s.id, marker);
  });
  if (located.length) {
    const bounds = L.latLngBounds(located.map((s) => [s.latitude, s.longitude]));
    map.fitBounds(bounds.pad(0.15), { maxZoom: 11 });
  }

  // Subjanela (modal) com os gráficos da estação clicada (mesma API de séries do detalhe).
  const modal = document.getElementById("map-modal");
  const panel = document.getElementById("map-panel");
  const closeBtn = document.getElementById("map-panel-close");
  let currentStation = null;

  function openMapPanel(s) {
    currentStation = s;
    document.getElementById("map-panel-title").textContent = `${s.name} (${s.id})`;
    document.getElementById("map-panel-link").href = `/stations/${encodeURIComponent(s.id)}`;
    renderPanelStatus(s.status, s.last_signal);
    const filesEl = document.getElementById("map-panel-files");
    // destrói as instâncias Chart.js antigas antes de descartar os canvases
    filesEl.querySelectorAll("canvas").forEach((c) => c._chart && c._chart.destroy());
    filesEl.innerHTML = "";
    if (!s.files.length) {
      filesEl.innerHTML = "<p class='empty'>Nenhum arquivo em monitoramento nesta estação.</p>";
    }
    s.files.forEach((f) => {
      // <details>: cada arquivo .dat (sensor/equipamento) pode ser recolhido
      const block = document.createElement("details");
      block.className = "file-block map-file-block";
      block.open = true;
      block.dataset.seriesUrl = f.series_url;
      block.innerHTML =
        `<summary><span class="file-chip">${esc(f.id)}</span>` +
        (f.alias ? `<span class="file-alias">${esc(f.alias)}</span>` : "") +
        `<span class="meta">${f.variables.length} variável(is)</span>` +
        `<span class="series-info meta"></span></summary>` +
        `<p class="series-error warn"></p>` +
        `<div class="charts">` +
        f.variables.map((_, i) =>
          `<div class="chart-box"><canvas data-var="${i}" height="110"></canvas></div>`).join("") +
        `</div>`;
      filesEl.appendChild(block);
      loadSeries(block);
    });
    modal.hidden = false;
    document.body.classList.add("map-modal-open"); // trava o scroll da página
  }

  function renderPanelStatus(status, lastSignal) {
    document.getElementById("map-panel-status").innerHTML =
      `<span class="dot dot-${esc(status)}"></span>${STATUS_LABEL[status] || esc(status)}` +
      ` · último sinal: ${esc(lastSignal || "—")}`;
  }

  function closeModal() {
    modal.hidden = true;
    document.body.classList.remove("map-modal-open");
    currentStation = null;
  }
  closeBtn.addEventListener("click", closeModal);
  document.getElementById("map-modal-backdrop").addEventListener("click", closeModal);
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && !modal.hidden) closeModal();
  });

  // Baixa uma imagem PNG com o retrato dos gráficos exibidos no instante do clique.
  function downloadPanelImage() {
    if (!currentStation) return;
    const canvases = Array.from(panel.querySelectorAll(".charts canvas"))
      .filter((c) => c._chart);
    if (!canvases.length) return;
    const scale = window.devicePixelRatio || 1;
    const pad = Math.round(16 * scale);
    const lineH = Math.round(20 * scale);
    const width = Math.max(...canvases.map((c) => c.width)) + pad * 2;
    const headerH = lineH * 2 + pad;
    const height = headerH + pad + canvases.reduce((a, c) => a + c.height + pad, 0);

    const out = document.createElement("canvas");
    out.width = width;
    out.height = height;
    const ctx = out.getContext("2d");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, width, height);

    const now = new Date();
    ctx.fillStyle = "#1e2a24";
    ctx.font = `bold ${Math.round(14 * scale)}px sans-serif`;
    ctx.fillText(`${currentStation.name} (${currentStation.id})`, pad, pad + lineH / 2);
    ctx.font = `${Math.round(11 * scale)}px sans-serif`;
    ctx.fillStyle = "#6b7a72";
    const statusTxt = STATUS_LABEL[currentStation.status] || currentStation.status;
    ctx.fillText(`${statusTxt} · imagem gerada em ${now.toLocaleString("pt-BR")}`,
                 pad, pad + lineH / 2 + lineH);

    let y = headerH + pad;
    canvases.forEach((c) => {
      ctx.drawImage(c, pad, y);
      y += c.height + pad;
    });

    const p2 = (n) => String(n).padStart(2, "0");  // hora LOCAL, igual à do cabeçalho
    const stamp = `${now.getFullYear()}-${p2(now.getMonth() + 1)}-${p2(now.getDate())}` +
                  `-${p2(now.getHours())}-${p2(now.getMinutes())}-${p2(now.getSeconds())}`;
    const a = document.createElement("a");
    a.download = `carbonara_${currentStation.id}_${stamp}.png`;
    a.href = out.toDataURL("image/png");
    a.click();
  }
  document.getElementById("map-panel-save").addEventListener("click", downloadPanelImage);

  // Estações georreferenciadas renderizadas nesta carga da página; se o conjunto
  // mudar (estação nova, removida ou coordenada alterada), a página recarrega e o
  // fitBounds refaz o enquadramento centrado em todas.
  const coordsKey = (list) => list
    .filter((s) => s.latitude != null && s.longitude != null)
    .map((s) => `${s.id}@${s.latitude},${s.longitude}`)
    .sort().join("|");
  const renderedKey = coordsKey(located);

  // Atualização periódica: cor dos marcadores via /api/status + séries do painel aberto.
  async function refresh() {
    try {
      const all = await (await fetch("/api/status")).json();
      if (coordsKey(all) !== renderedKey && modal.hidden) {
        location.reload(); // não interrompe uma subjanela aberta
        return;
      }
      all.forEach((st) => {
        const marker = markers.get(st.id);
        const s = located.find((x) => x.id === st.id);
        if (marker && s) {
          s.status = st.status;
          s.last_signal = st.last_signal;
          marker.setStyle({ fillColor: statusColor(st.status) });
          // balão aberto: não reconstruir o conteúdo (resetaria a rolagem do usuário)
          if (!marker.isTooltipOpen()) {
            marker.setTooltipContent(markerTooltip(s, lastVals.get(st.id) || null, false));
          }
        }
        if (currentStation && st.id === currentStation.id) {
          renderPanelStatus(st.status, st.last_signal);
        }
      });
    } catch (e) { /* rede indisponível: mantém o último estado */ }
    if (currentStation && !modal.hidden) {
      panel.querySelectorAll(".map-file-block[data-series-url]").forEach(loadSeries);
    }
  }
  setInterval(refresh, (window.REFRESH_SECONDS || 60) * 1000);
}


/* --- Formulário de arquivo: variáveis dinâmicas + preview ---------------------- */
function initFileForm() {
  const form = document.getElementById("file-form");
  if (!form) return;

  const tbody = document.querySelector("#vars-table tbody");

  /* Próximo índice livre: máximo dos existentes + 1, pulando a coluna do timestamp. */
  function nextIndex() {
    const cols = Array.from(tbody.querySelectorAll('input[name="var_column"]'))
      .map((inp) => parseInt(inp.value, 10)).filter(Number.isFinite);
    let next = cols.length ? Math.max(...cols) + 1 : 0;
    const ts = parseInt(form.elements.ts_column.value, 10);
    if (Number.isFinite(ts) && next === ts) next++;
    return next;
  }

  function makeVarRow(col, label = "", unit = "", min = "", max = "") {
    const tr = document.createElement("tr");
    tr.innerHTML =
      '<td><input name="var_column" class="num-input" ' +
      'title="Índice da coluna (0 = primeira, -1 = última) ou o nome dela"></td>' +
      '<td><select name="var_type">' +
      '<option value="numero">Número</option><option value="inteiro">Inteiro</option>' +
      '<option value="booleano">Booleano</option>' +
      '<option value="epoch">Data/hora (epoch)</option>' +
      '<option value="timestamp">Data/hora (texto)</option>' +
      '<option value="texto">Texto</option></select></td>' +
      '<td><input name="var_label"></td>' +
      '<td><input name="var_unit"></td>' +
      '<td><input name="var_min" placeholder="—" class="num-input"></td>' +
      '<td><input name="var_max" placeholder="—" class="num-input"></td>' +
      '<td><button type="button" class="btn btn-danger btn-remove-var">×</button></td>';
    tr.querySelector('[name="var_column"]').value = col;
    tr.querySelector('[name="var_label"]').value = label;
    tr.querySelector('[name="var_unit"]').value = unit;
    tr.querySelector('[name="var_min"]').value = min;
    tr.querySelector('[name="var_max"]').value = max;
    return tr;
  }

  document.getElementById("btn-add-var").addEventListener("click", () => {
    tbody.appendChild(makeVarRow(nextIndex()));
  });

  /* Lista TODAS as colunas do arquivo (via preview), preservando as já configuradas
     — casa por índice ou pelo nome da coluna (configs antigas por nome). */
  document.getElementById("btn-fill-cols").addEventListener("click", async () => {
    const area = document.getElementById("preview-area");
    area.innerHTML = "<p class='meta'>Lendo as colunas do arquivo…</p>";
    try {
      const resp = await fetch("/api/preview", { method: "POST", body: new FormData(form) });
      const data = await resp.json();
      if (data.error) { area.innerHTML = `<p class="warn">${esc(data.error)}</p>`; return; }
      const header = data.header;
      const ncols = Math.max(...(data.rows || []).map((r) => r.length),
                             (header || []).length);
      if (!Number.isFinite(ncols) || ncols < 1) {
        area.innerHTML = "<p class='warn'>Não foi possível determinar as colunas do arquivo.</p>";
        return;
      }
      const clean = (h) => (h || "").trim().replace(/^"|"$/g, "");
      const tsRaw = (form.elements.ts_column.value || "0").trim();
      let tsIdx = /^-?\d+$/.test(tsRaw) ? parseInt(tsRaw, 10) : -1;
      if (tsIdx < 0 && header) tsIdx = header.findIndex((h) => clean(h) === tsRaw);
      const existing = new Map();
      Array.from(tbody.rows).forEach((tr) => {
        const key = tr.querySelector('[name="var_column"]').value.trim();
        if (key === "") return;
        existing.set(key, {
          label: tr.querySelector('[name="var_label"]').value,
          unit: tr.querySelector('[name="var_unit"]').value,
          min: tr.querySelector('[name="var_min"]').value,
          max: tr.querySelector('[name="var_max"]').value,
        });
      });
      tbody.innerHTML = "";
      for (let i = 0; i < ncols; i++) {
        if (i === tsIdx) continue;
        const name = header ? clean(header[i]) : "";
        const prev = existing.get(String(i)) || (name && existing.get(name)) || null;
        tbody.appendChild(makeVarRow(i,
          prev ? prev.label : (name || `coluna ${i}`),
          prev ? prev.unit : "", prev ? prev.min : "", prev ? prev.max : ""));
      }
      area.innerHTML = `<p class="diag" style="color:var(--green)">✓ ${tbody.rows.length} ` +
        "colunas listadas (a do timestamp ficou de fora). Ajuste rótulos, unidades e " +
        "limites e clique em Salvar.</p>";
    } catch (e) {
      area.innerHTML = `<p class="warn">Falha ao listar as colunas: ${esc(String(e))}</p>`;
    }
  });

  document.getElementById("vars-table").addEventListener("click", (ev) => {
    if (!ev.target.classList.contains("btn-remove-var")) return;
    const tbody = document.querySelector("#vars-table tbody");
    if (tbody.rows.length > 1) ev.target.closest("tr").remove();
  });

  document.getElementById("btn-preview").addEventListener("click", async () => {
    const area = document.getElementById("preview-area");
    area.innerHTML = "<p class='meta'>Lendo o final do arquivo…</p>";
    try {
      const resp = await fetch("/api/preview", { method: "POST", body: new FormData(form) });
      const data = await resp.json();
      if (data.error) { area.innerHTML = `<p class="warn">${esc(data.error)}</p>`; return; }
      let html = "";
      const diags = data.diagnostics || [], rows = data.rows || [];
      diags.forEach((d) => (html += `<p class="warn diag">⚠ ${esc(d)}</p>`));
      if (!diags.length) html += `<p class="diag" style="color:var(--green)">✓ Parse OK.</p>`;

      /* Mapa coluna(índice) -> rótulo configurado NO FORMULÁRIO (variáveis + timestamp),
         para o preview mostrar EM QUAL coluna cada rótulo (ex.: RECORD) caiu. Resolve
         tanto índice numérico quanto nome de coluna (configs por nome, via header). */
      const clean = (h) => (h || "").trim().replace(/^"|"$/g, "");
      const resolveIdx = (raw) => {
        const s = (raw || "").trim();
        if (/^-?\d+$/.test(s)) return parseInt(s, 10);
        if (data.header) { const i = data.header.findIndex((h) => clean(h) === s); return i >= 0 ? i : null; }
        return null;
      };
      const colLabels = new Map();
      const tsIdx = resolveIdx(form.elements.ts_column ? form.elements.ts_column.value : "");
      if (tsIdx !== null && Number.isFinite(tsIdx)) colLabels.set(tsIdx, { label: "carimbo de tempo", ts: true });
      Array.from(tbody.rows).forEach((tr) => {
        const colInp = tr.querySelector('[name="var_column"]');
        if (!colInp) return;
        const idx = resolveIdx(colInp.value);
        if (idx === null || !Number.isFinite(idx)) return;
        const labInp = tr.querySelector('[name="var_label"]');
        colLabels.set(idx, { label: (labInp && labInp.value) || `coluna ${idx}`, ts: false });
      });
      const cellCls = (c) => (colLabels.has(c) ? (colLabels.get(c).ts ? " class='col-mapped col-ts'" : " class='col-mapped'") : "");

      html += "<p class='meta'>Colunas destacadas = mapeadas no formulário (rótulo abaixo do número); " +
        "azul = carimbo de tempo. As demais ainda não têm rótulo.</p>";
      html += "<div style='overflow-x:auto'><table class='table'><thead><tr>";
      const ncols = Math.max(...rows.map((r) => r.length), (data.header || []).length);
      for (let c = 0; c < ncols; c++) {
        const name = data.header && data.header[c] ? ` · ${esc(data.header[c])}` : "";
        const lab = colLabels.get(c);
        const labPart = lab ? `<br><span class='col-label'>${esc(lab.label)}</span>` : "";
        html += `<th${cellCls(c)}>${c}${name}${labPart}</th>`;
      }
      html += "</tr></thead><tbody>";
      /* Na coluna do carimbo, mostra o valor cru (conferência do parse) e embaixo a
         data/hora convertida — é assim que se vê num relance se um epoch virou a data
         certa, ou se o formato não casou. */
      const tsIndex = (data.ts_index === null || data.ts_index === undefined) ? -1 : data.ts_index;
      const tsDisp = data.ts_display || [];
      const colDisp = data.col_display || {};   /* {coluna: [convertido por linha]} */
      rows.forEach((r, ri) => {
        html += "<tr>" + Array.from({ length: ncols }, (_, c) => {
          let v = esc(r[c] ?? "");
          if (c === tsIndex) {
            v += tsDisp[ri]
              ? `<br><span class='col-label'>${esc(tsDisp[ri])}</span>`
              : "<br><span class='warn'>não convertido</span>";
          } else if (colDisp[c]) {
            /* coluna mapeada com tipo (epoch, timestamp, booleano, inteiro) */
            v += colDisp[c][ri]
              ? `<br><span class='col-label'>${esc(colDisp[c][ri])}</span>`
              : "<br><span class='warn'>não convertido</span>";
          }
          return `<td${cellCls(c)}>${v}</td>`;
        }).join("") + "</tr>";
      });
      html += "</tbody></table></div>";
      area.innerHTML = html;
    } catch (e) {
      area.innerHTML = `<p class="warn">Falha no preview: ${esc(String(e))}</p>`;
    }
  });
}

/* --- Listagem dos arquivos da estação (tela Editar estação) -------------------- */
function fmtSize(n) {
  if (n == null) return "—";
  if (n < 1024) return n + " B";
  if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
  return (n / 1048576).toFixed(1) + " MB";
}

function initStationBrowser() {
  const form = document.getElementById("browse-form");
  if (!form) return;
  const results = document.getElementById("browse-results");
  const crumbsEl = document.getElementById("browse-crumbs");
  const hint = document.getElementById("browse-hint");
  const filter = document.getElementById("browse-filter");
  const count = document.getElementById("browse-count");
  const payload = document.getElementById("browse-payload");
  const root = form.dataset.root || "incoming";

  // Seleção acumulada: caminho a gravar no config -> {alias, rel}. Vive fora da
  // tabela de propósito — é o que faz a marcação sobreviver à troca de pastas.
  const picked = new Map();
  let current = { files: [], rel: "" };

  function selCount() {
    const n = picked.size;
    count.textContent = n
      ? `${n} arquivo(s) selecionado(s) — de qualquer pasta; clique em "Adicionar selecionados".`
      : "Nenhum arquivo selecionado ainda.";
  }

  function applyFilter() {
    const q = (filter.value || "").trim().toLowerCase();
    let shown = 0;
    results.querySelectorAll("tr[data-search]").forEach((tr) => {
      const hit = !q || tr.dataset.search.includes(q);
      tr.hidden = !hit;
      if (hit) shown += 1;
    });
    const total = results.querySelectorAll("tr[data-search]").length;
    if (q && shown !== total) {
      hint.innerHTML = `<span class="meta">${shown} de ${total} item(ns) exibido(s) ` +
        `nesta pasta — filtro "${esc(filter.value.trim())}". O filtro só esconde linhas; ` +
        "a seleção continua valendo.</span>";
    }
  }

  async function load(sub) {
    results.innerHTML = "<p class='empty'>Carregando…</p>";
    let data;
    try {
      const url = form.dataset.url + (sub ? "?sub=" + encodeURIComponent(sub) : "");
      data = await (await fetch(url)).json();
    } catch (e) {
      results.innerHTML = `<p class="warn">Falha ao listar a pasta: ${esc(String(e))}</p>`;
      return;
    }
    if (data.error) {
      results.innerHTML = `<p class="warn">${esc(data.error)}</p>`;
      return;
    }
    current = data;
    filter.value = "";
    render(data);
    selCount();
  }

  function render(d) {
    // Trilho de navegação, igual ao do navegador da aba Coleta.
    let cr = `<a href="#" data-go="">${esc(root)}/</a>`;
    (d.crumbs || []).forEach((c) => {
      cr += ` / <a href="#" data-go="${esc(c.rel)}">${esc(c.name)}</a>`;
    });
    crumbsEl.innerHTML = cr;

    if (d.missing) {
      results.innerHTML = "<p class='empty'>Esta pasta ainda não existe no servidor " +
        "(nada foi recebido da estação até agora).</p>";
      return;
    }
    const dirs = d.dirs || [];
    const files = d.files || [];
    if (!dirs.length && !files.length) {
      results.innerHTML = "<p class='empty'>Pasta vazia.</p>";
      return;
    }

    let html = "<table class='table'><thead><tr><th></th><th>Nome</th><th>Tipo</th>" +
      "<th>Tamanho</th><th>Modificado</th><th>Apelido (monitoramento)</th><th></th>" +
      "</tr></thead><tbody>";
    if (d.rel) {
      const parts = d.rel.split("/");
      parts.pop();
      html += "<tr><td colspan='7'>" +
        `<a href="#" data-go="${esc(parts.join("/"))}">↩ pasta acima</a></td></tr>`;
    }
    dirs.forEach((dir) => {
      html += `<tr data-search="${esc(dir.name.toLowerCase())}">` +
        "<td></td>" +
        `<td>📁 <a href="#" data-go="${esc(dir.rel)}"><strong>${esc(dir.name)}/</strong></a>` +
        (dir.hidden ? " <small class='meta'>(oculto — a ingestão ignora)</small>" : "") +
        "</td><td class='meta'>pasta</td><td class='meta'>—</td>" +
        `<td><small>${esc(dir.mtime)}</small></td><td></td><td></td></tr>`;
    });
    files.forEach((f) => {
      // Ocultos aparecem (como na Coleta) mas não são monitoráveis: a ingestão os pula.
      const blocked = f.monitored || f.hidden;
      const sel = picked.has(f.path);
      const alias = sel ? picked.get(f.path).alias : (f.suggested_alias || "");
      let note = "";
      if (f.monitored) note = " <small class='meta'>(já monitorado)</small>";
      else if (f.hidden) note = " <small class='meta'>(oculto — a ingestão ignora)</small>";
      html += `<tr data-search="${esc((f.name + " " + (f.ext || "")).toLowerCase())}">` +
        `<td><input type="checkbox" class="browse-pick" data-path="${esc(f.path)}" ` +
        `data-rel="${esc(f.rel)}" ${blocked ? "disabled" : ""} ${sel ? "checked" : ""}></td>` +
        `<td><code>${esc(f.name)}</code>${note}</td>` +
        `<td class="meta">${esc(f.ext || "—")}</td>` +
        `<td class="meta">${fmtSize(f.size)}</td>` +
        `<td><small>${esc(f.mtime || "—")}</small></td>` +
        `<td><input class="browse-alias" data-path="${esc(f.path)}" value="${esc(alias)}" ` +
        `${blocked ? "disabled" : ""}></td>` +
        `<td class="ta-right"><a class="btn btn-sm" ` +
        `href="${form.dataset.download}?sub=${encodeURIComponent(f.rel)}">baixar</a></td>` +
        "</tr>";
    });
    html += "</tbody></table>";
    if (d.truncated) {
      html += `<p class="flash flash-error">Listagem truncada em ${d.max_entries} ` +
        "entradas — esta pasta tem mais itens; use as subpastas ou o filtro.</p>";
    }
    results.innerHTML = html;
  }

  // Navegação entre pastas e trilho: um só listener (a tabela é reconstruída a cada
  // troca de pasta, então delegar evita religar handlers).
  function onNav(ev) {
    const link = ev.target.closest("[data-go]");
    if (!link) return;
    ev.preventDefault();
    load(link.dataset.go);
  }
  results.addEventListener("click", onNav);
  crumbsEl.addEventListener("click", onNav);

  results.addEventListener("change", (ev) => {
    const box = ev.target.closest(".browse-pick");
    if (!box) return;
    const path = box.dataset.path;
    if (box.checked) {
      const row = box.closest("tr");
      const alias = row.querySelector(".browse-alias");
      picked.set(path, { alias: (alias && alias.value.trim()) || "", rel: box.dataset.rel });
    } else {
      picked.delete(path);
    }
    selCount();
  });

  results.addEventListener("input", (ev) => {
    const inp = ev.target.closest(".browse-alias");
    if (!inp) return;
    const entry = picked.get(inp.dataset.path);
    if (entry) entry.alias = inp.value.trim();
  });

  filter.addEventListener("input", applyFilter);
  // Enter no filtro é para filtrar, não para enviar o formulário.
  filter.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") ev.preventDefault();
  });

  // Marcar/desmarcar age só nas linhas VISÍVEIS — combina com o filtro.
  document.getElementById("btn-browse-all").addEventListener("click", () => {
    const boxes = Array.from(results.querySelectorAll(".browse-pick:not([disabled])"))
      .filter((b) => !b.closest("tr").hidden);
    const turnOn = boxes.some((b) => !b.checked);
    boxes.forEach((b) => {
      b.checked = turnOn;
      b.dispatchEvent(new Event("change", { bubbles: true }));
    });
  });

  document.getElementById("btn-browse-clear").addEventListener("click", () => {
    picked.clear();
    results.querySelectorAll(".browse-pick").forEach((b) => {
      if (!b.disabled) b.checked = false;
    });
    selCount();
  });

  form.addEventListener("submit", (ev) => {
    if (!picked.size) {
      ev.preventDefault();
      hint.innerHTML = "<span class='warn'>Marque ao menos um arquivo antes de " +
        "adicionar.</span>";
      return;
    }
    // Monta os campos que o servidor espera (pick + path_<n> + alias_<n>) a partir da
    // seleção acumulada — inclusive de pastas que não estão na tela agora.
    payload.innerHTML = "";
    let i = 0;
    picked.forEach((v, path) => {
      payload.insertAdjacentHTML("beforeend",
        `<input type="hidden" name="pick" value="${i}">` +
        `<input type="hidden" name="path_${i}" value="${esc(path)}">` +
        `<input type="hidden" name="alias_${i}" value="${esc(v.alias)}">`);
      i += 1;
    });
  });

  load("");   // listagem inicial: a pasta raiz da estação, já ao abrir a tela
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (ch) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}
