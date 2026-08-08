const $ = (selector) => document.querySelector(selector);

async function request(path) {
  const response = await fetch(path);
  const contentType = response.headers.get("content-type") || "";
  const text = await response.text();
  if (!contentType.includes("application/json")) {
    const hint = text.trim().startsWith("<")
      ? "接口返回了页面内容，通常是看板后端还没有重启。"
      : "接口没有返回 JSON 数据。";
    throw new Error(`${hint}请重启教师工作台后再刷新。`);
  }
  let payload;
  try {
    payload = JSON.parse(text);
  } catch {
    throw new Error("接口返回内容无法解析，请重启教师工作台后再刷新。");
  }
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

function metricById(metrics) {
  return new Map((metrics || []).map((metric) => [metric.id, metric]));
}

function pointFromSummary(summary) {
  const week = summary.current_week || { week: 1, courses: [1, 2] };
  const metrics = metricById(summary.metrics || []);
  const finished = metrics.get("finished") || {};
  const absent = metrics.get("absent") || {};
  const arrivedUnfinished = metrics.get("arrived_unfinished") || {};
  const firstLessonUnfinished = metrics.get("first_lesson_unfinished") || {};
  const total = Number((metrics.get("all") || {}).count || 0);
  return {
    week: Number(week.week || 1),
    label: `W${Number(week.week || 1)}`,
    courses: week.courses || [Number(week.week || 1) * 2 - 1, Number(week.week || 1) * 2],
    total,
    finished: Number(finished.count || 0),
    finished_rate: Number(finished.percent || 0),
    absent: Number(absent.count || 0),
    absent_rate: Number(absent.percent || 0),
    arrived_unfinished: Number(arrivedUnfinished.count || 0),
    arrived_unfinished_rate: Number(arrivedUnfinished.percent || 0),
    first_lesson_unfinished: Number(firstLessonUnfinished.count || 0),
    first_lesson_unfinished_rate: Number(firstLessonUnfinished.percent || 0),
    live_rate: null,
    fetched_at: summary.course_fetched_at,
    source: "api/summary fallback",
  };
}

function defaultSeries() {
  return [
    { key: "finished_rate", label: "完课率", color: "#367a4b" },
    { key: "absent_rate", label: "未到课率", color: "#bd4b45" },
    { key: "arrived_unfinished_rate", label: "到课未完课率", color: "#d69b2d" },
    { key: "first_lesson_unfinished_rate", label: "第一课未完课率", color: "#5c7cfa" },
    { key: "live_rate", label: "直播参与率", color: "#6b8e23", optional: true },
  ];
}

async function loadSummaryFallback(originalError) {
  const summary = await request("/api/summary");
  const point = pointFromSummary(summary);
  return {
    checked_at: summary.checked_at || "",
    points: [point],
    series: defaultSeries(),
    message: `趋势接口暂未就绪，已先展示当前周快照。${originalError.message}`,
    fallback: true,
  };
}

function renderTrendData(data) {
  $("#trendCheckedAt").textContent = data.checked_at ? `检查于 ${data.checked_at.split(" ")[1] || data.checked_at}` : "已读取本地数据";
  $("#trendMessage").textContent = data.message || "已读取本地缓存。";
  renderLegend(data.series || []);
  renderChart(data.points || [], data.series || []);
  renderStats(data.points || []);
  renderTable(data.points || []);
}

async function loadTrends() {
  try {
    renderTrendData(await request("/api/trends"));
  } catch (error) {
    try {
      const fallback = await loadSummaryFallback(error);
      renderTrendData(fallback);
      showToast("趋势接口暂未就绪，已先展示当前周快照。");
    } catch (fallbackError) {
      const message = `${error.message} ${fallbackError.message}`;
      $("#trendCheckedAt").textContent = "读取失败";
      $("#trendMessage").textContent = "没有拿到可用数据。请重启教师工作台后再刷新。";
      $("#trendChart").innerHTML = `<div class="empty-state">${escapeHtml(message)}</div>`;
      $("#trendStats").innerHTML = '<div class="empty-state">加载失败，请重启看板后刷新</div>';
      $("#trendTable").innerHTML = '<div class="empty-state">加载失败，请重启看板后刷新</div>';
      showToast("趋势数据加载失败，请重启看板。");
    }
  }
}

$("#refreshTrends").addEventListener("click", loadTrends);
loadTrends();
