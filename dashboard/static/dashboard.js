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
function makeOrUpdateChart(canvas, labels, v, color, opts = {}) {
  const label = v.unit ? `${v.label} (${v.unit})` : v.label;
  const limits = { min: v.min ?? null, max: v.max ?? null };
  if (canvas._chart) {
    const ch = canvas._chart;
    ch.$limits = limits;
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
        // trechos fora dos limites físicos ficam vermelhos (dado ruim à primeira vista)
        segment: {
          borderColor: (s) =>
            outOfLimits(canvas._chart && canvas._chart.$limits, s.p1.parsed.y) ||
            outOfLimits(canvas._chart && canvas._chart.$limits, s.p0.parsed.y)
              ? LIMIT_COLOR : undefined,
        },
      }],
    },
    options: {
      animation: false, responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: !!opts.legend } },
      scales: {
        x: { ticks: { maxTicksLimit: opts.legend ? 10 : 6, autoSkip: true } },
        y: { suggestedMin: limits.min ?? undefined, suggestedMax: limits.max ?? undefined },
      },
    },
  });
  chart.$limits = limits;
  canvas._chart = chart;
  if (opts.height) canvas.parentElement.style.height = opts.height;
}

document.addEventListener("DOMContentLoaded", () => {
  initCharts();
  initCompare();
  initFileForm();
});

/* --- Gráficos (página de detalhe da estação) ---------------------------------- */
function initCharts() {
  const blocks = document.querySelectorAll(".file-block[data-series-url]");
  if (!blocks.length) return;
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
    if (data.error) { errEl.textContent = data.error; return; }
    errEl.textContent = (data.problems || []).join(" · ");
    infoEl.textContent = ` · ${data.rows_read} linhas lidas` +
      (data.decimated ? " (decimado p/ o gráfico)" : "") +
      ` · atualizado ${data.generated_at}`;

    data.variables.forEach((v, i) => {
      const canvas = block.querySelector(`canvas[data-var="${i}"]`);
      if (!canvas) return;
      makeOrUpdateChart(canvas, data.timestamps, v, PALETTE[i % PALETTE.length],
                        { legend: true, height: "230px" });
    });
  } catch (e) {
    errEl.textContent = "Falha ao buscar os dados: " + e;
  }
}

/* --- Tela Comparar: várias estações, uma variável por gráfico ------------------ */
function initCompare() {
  const canvases = document.querySelectorAll(".cmp-canvas[data-series-url]");
  if (!canvases.length) return;
  const refresh = () => {
    // Um fetch por arquivo, mesmo com várias variáveis dele selecionadas.
    const byUrl = new Map();
    canvases.forEach((c) => {
      const url = c.dataset.seriesUrl;
      if (!byUrl.has(url)) byUrl.set(url, []);
      byUrl.get(url).push(c);
    });
    byUrl.forEach(async (group, url) => {
      let data;
      try {
        data = await (await fetch(url)).json();
      } catch (e) {
        group.forEach((c) => setCmpError(c, "Falha ao buscar os dados: " + e));
        return;
      }
      group.forEach((c) => {
        if (data.error) { setCmpError(c, data.error); return; }
        const v = data.variables[parseInt(c.dataset.var, 10)];
        if (!v) { setCmpError(c, "Variável não encontrada na série."); return; }
        setCmpError(c, (data.problems || []).join(" · "));
        makeOrUpdateChart(c, data.timestamps, v,
                          PALETTE[parseInt(c.dataset.color, 10) % PALETTE.length]);
      });
    });
  };
  refresh();
  setInterval(refresh, (window.REFRESH_SECONDS || 60) * 1000);
}

function setCmpError(canvas, msg) {
  const el = canvas.closest(".cmp-block").querySelector(".series-error");
  if (el) el.textContent = msg || "";
}


/* --- Formulário de arquivo: variáveis dinâmicas + preview ---------------------- */
function initFileForm() {
  const form = document.getElementById("file-form");
  if (!form) return;

  document.getElementById("btn-add-var").addEventListener("click", () => {
    const tbody = document.querySelector("#vars-table tbody");
    const row = tbody.rows[0].cloneNode(true);
    row.querySelectorAll("input").forEach((inp) => (inp.value = ""));
    tbody.appendChild(row);
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
      (data.diagnostics || []).forEach((d) => (html += `<p class="warn diag">⚠ ${esc(d)}</p>`));
      if (!data.diagnostics.length) html += `<p class="diag" style="color:var(--green)">✓ Parse OK.</p>`;
      html += "<table class='table'><thead><tr>";
      const ncols = Math.max(...data.rows.map((r) => r.length), (data.header || []).length);
      for (let c = 0; c < ncols; c++) {
        const name = data.header && data.header[c] ? ` · ${esc(data.header[c])}` : "";
        html += `<th>${c}${name}</th>`;
      }
      html += "</tr></thead><tbody>";
      data.rows.forEach((r) => {
        html += "<tr>" + Array.from({ length: ncols }, (_, c) => `<td>${esc(r[c] ?? "")}</td>`).join("") + "</tr>";
      });
      html += "</tbody></table>";
      area.innerHTML = html;
    } catch (e) {
      area.innerHTML = `<p class="warn">Falha no preview: ${esc(String(e))}</p>`;
    }
  });
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (ch) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}
