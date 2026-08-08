const $ = (selector) => document.querySelector(selector);

async function request(path) {
  const response = await fetch(path);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `请求失败：${response.status}`);
  return payload;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove("show"), 2600);
}

function formatPercent(value) {
  return value === null || value === undefined ? "暂无" : `${Number(value).toFixed(1).replace(".0", "")}%`;
}

function renderLegend(series) {
  $("#trendLegend").innerHTML = series.map((item) => `
    <span class="trend-legend-item">
      <i style="background:${escapeHtml(item.color)}"></i>${escapeHtml(item.label)}
    </span>
  `).join("");
}

function pointPosition(pointIndex, pointCount, value, width, height, padding) {
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  const x = pointCount <= 1
    ? padding.left + chartWidth / 2
    : padding.left + (chartWidth * pointIndex) / (pointCount - 1);
  const y = padding.top + chartHeight - (Math.max(0, Math.min(100, Number(value))) / 100) * chartHeight;
  return { x, y };
}

function renderChart(points, series) {
  const container = $("#trendChart");
  if (!points.length) {
    container.innerHTML = '<div class="empty-state">暂无可用于趋势图的数据。先更新一次完课和直播后再回来看看。</div>';
    return;
  }
  const width = 880;
  const height = 360;
  const padding = { top: 24, right: 36, bottom: 54, left: 54 };
  const chartHeight = height - padding.top - padding.bottom;
  const yTicks = [0, 25, 50, 75, 100];
  const activeSeries = series.filter((item) => points.some((point) => point[item.key] !== null && point[item.key] !== undefined));
  const grid = yTicks.map((tick) => {
    const y = padding.top + chartHeight - (tick / 100) * chartHeight;
    return `
      <g>
        <line x1="${padding.left}" y1="${y}" x2="${width - padding.right}" y2="${y}" class="trend-grid-line"></line>
        <text x="${padding.left - 12}" y="${y + 4}" class="trend-axis-label" text-anchor="end">${tick}%</text>
      </g>
    `;
  }).join("");
  const xLabels = points.map((point, index) => {
    const { x } = pointPosition(index, points.length, 0, width, height, padding);
    return `
      <g>
        <text x="${x}" y="${height - 24}" class="trend-axis-label" text-anchor="middle">${escapeHtml(point.label)}</text>
        <text x="${x}" y="${height - 8}" class="trend-axis-sub" text-anchor="middle">第${point.courses[0]}-${point.courses[1]}课</text>
      </g>
    `;
  }).join("");
  const lines = activeSeries.map((item) => {
    const usable = points
      .map((point, index) => ({ point, index }))
      .filter(({ point }) => point[item.key] !== null && point[item.key] !== undefined);
    const path = usable.map(({ point, index }, pathIndex) => {
      const { x, y } = pointPosition(index, points.length, point[item.key], width, height, padding);
      return `${pathIndex === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
    }).join(" ");
    const dots = usable.map(({ point, index }) => {
      const { x, y } = pointPosition(index, points.length, point[item.key], width, height, padding);
      return `<circle cx="${x}" cy="${y}" r="4.5" fill="${escapeHtml(item.color)}"><title>${escapeHtml(point.label)} ${escapeHtml(item.label)} ${formatPercent(point[item.key])}</title></circle>`;
    }).join("");
    return `
      <path d="${path}" fill="none" stroke="${escapeHtml(item.color)}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"></path>
      ${dots}
    `;
  }).join("");
  container.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" class="trend-svg" preserveAspectRatio="none">
      ${grid}
      <line x1="${padding.left}" y1="${height - padding.bottom}" x2="${width - padding.right}" y2="${height - padding.bottom}" class="trend-axis-line"></line>
      ${xLabels}
      ${lines}
    </svg>
  `;
}

function renderStats(points) {
  const latest = points.at(-1);
  if (!latest) {
    $("#trendStats").innerHTML = '<div class="empty-state">暂无趋势数据</div>';
    $("#trendLatestWeek").textContent = "--";
    return;
  }
  $("#trendLatestWeek").textContent = `${latest.label} · ${latest.total} 人`;
  const stats = [
    ["完课率", latest.finished_rate, latest.finished],
    ["未到课率", latest.absent_rate, latest.absent],
    ["到课未完课率", latest.arrived_unfinished_rate, latest.arrived_unfinished],
    ["直播参与率", latest.live_rate, null],
  ];
  $("#trendStats").innerHTML = stats.map(([label, rate, count]) => `
    <div class="trend-stat">
      <span>${escapeHtml(label)}</span>
      <strong>${formatPercent(rate)}</strong>
      <small>${count === null ? "来自直播缓存" : `${escapeHtml(count)} 人`}</small>
    </div>
  `).join("");
}

function renderTable(points) {
  if (!points.length) {
    $("#trendTable").innerHTML = '<div class="empty-state">暂无周数据明细</div>';
    return;
  }
  $("#trendTable").innerHTML = `
    <table class="trend-table">
      <thead>
        <tr>
          <th>周次</th>
          <th>课程</th>
          <th>总人数</th>
          <th>已完课</th>
          <th>未到课</th>
          <th>到课未完课</th>
          <th>直播参与</th>
          <th>数据来源</th>
        </tr>
      </thead>
      <tbody>
        ${points.map((point) => `
          <tr>
            <td>${escapeHtml(point.label)}</td>
            <td>第${escapeHtml(point.courses[0])}-${escapeHtml(point.courses[1])}课</td>
            <td>${escapeHtml(point.total)}</td>
            <td>${escapeHtml(point.finished)} <span>${formatPercent(point.finished_rate)}</span></td>
            <td>${escapeHtml(point.absent)} <span>${formatPercent(point.absent_rate)}</span></td>
            <td>${escapeHtml(point.arrived_unfinished)} <span>${formatPercent(point.arrived_unfinished_rate)}</span></td>
            <td>${formatPercent(point.live_rate)}</td>
            <td><code>${escapeHtml(point.source)}</code></td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

async function loadTrends() {
  try {
    const data = await request("/api/trends");
    $("#trendCheckedAt").textContent = `检查于 ${data.checked_at.split(" ")[1]}`;
    $("#trendMessage").textContent = data.message || "已读取本地缓存。";
    renderLegend(data.series || []);
    renderChart(data.points || [], data.series || []);
    renderStats(data.points || []);
    renderTable(data.points || []);
  } catch (error) {
    $("#trendChart").innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
    $("#trendStats").innerHTML = '<div class="empty-state">加载失败</div>';
    $("#trendTable").innerHTML = '<div class="empty-state">加载失败</div>';
    showToast(error.message);
  }
}

$("#refreshTrends").addEventListener("click", loadTrends);
loadTrends();
