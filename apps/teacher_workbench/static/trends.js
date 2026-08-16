const $ = (selector) => document.querySelector(selector);

let trendChart = null;
let trendData = null;
let trendUpdateTask = null;
let activeJobId = null;
let trendPollTimer = null;
let activeTrendStudents = [];

const trendMetricLabels = {
  all: "总人数",
  finished: "已完课",
  absent: "未到课",
  arrived_unfinished: "到课未完课",
  first_lesson_unfinished: "第一课未完课",
};

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
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

function setTrendUpdateStatus(message) {
  const target = $("#trendUpdateStatus");
  if (target) target.textContent = message || "";
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
  const weekNumber = Number(week.week || 1);
  return {
    week: weekNumber,
    label: `W${weekNumber}`,
    courses: week.courses || [weekNumber * 2 - 1, weekNumber * 2],
    total: Number((metrics.get("all") || {}).count || 0),
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
    students_by_metric: Object.fromEntries((summary.metrics || []).map((metric) => [metric.id, metric.students || []])),
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
  return {
    checked_at: summary.checked_at || "",
    points: [pointFromSummary(summary)],
    series: defaultSeries(),
    message: `趋势接口暂未就绪，已先展示当前周快照。${originalError.message}`,
    fallback: true,
  };
}

async function loadTrendUpdateTask() {
  if (trendUpdateTask) return trendUpdateTask;
  const data = await request("/api/tasks");
  const tasks = data.tasks || [];
  trendUpdateTask = tasks.find((task) => task.id === "completion_and_live_w1")
    || tasks.find((task) => /同时更新完课和直播|完课.*直播/.test(`${task.title || ""} ${task.description || ""}`));
  if (!trendUpdateTask) {
    throw new Error("没有找到可用于更新趋势的任务，请重启教师工作台后再试。");
  }
  return trendUpdateTask;
}

function setUpdateButtonDisabled(disabled) {
  const updateButton = $("#updateTrendData");
  const refreshButton = $("#refreshTrends");
  if (updateButton) updateButton.disabled = disabled;
  if (refreshButton) refreshButton.disabled = disabled;
}

async function trendUpdateWeeks() {
  try {
    const summary = await request("/api/summary");
    const weeks = (summary.available_weeks || [])
      .map((week) => Number(week.week))
      .filter((week) => Number.isFinite(week) && week > 0);
    if (weeks.length) return [...new Set(weeks)].sort((left, right) => left - right);
    const current = Number(summary.current_week?.week || 0);
    if (current > 0) return Array.from({ length: current }, (_, index) => index + 1);
  } catch {
    // Fall back to currently rendered trend points below.
  }
  const pointWeeks = (trendData?.points || [])
    .map((point) => Number(point.week))
    .filter((week) => Number.isFinite(week) && week > 0);
  if (pointWeeks.length) {
    const maxWeek = Math.max(...pointWeeks);
    return Array.from({ length: maxWeek }, (_, index) => index + 1);
  }
  return [1];
}

async function pollTrendUpdate(jobId) {
  try {
    const job = await request(`/api/jobs/${encodeURIComponent(jobId)}`);
    const lastLog = (job.logs || []).at(-1) || "";
    setTrendUpdateStatus(lastLog || `${job.title || "更新任务"}运行中…`);
    if (job.status === "running") {
      trendPollTimer = setTimeout(() => pollTrendUpdate(jobId), 1600);
      return;
    }
    activeJobId = null;
    setUpdateButtonDisabled(false);
    if (job.status === "success") {
      setTrendUpdateStatus("趋势数据已更新，正在刷新图表…");
      await loadTrends();
      showToast("趋势数据已更新。");
      setTrendUpdateStatus("更新完成，图表已刷新。");
    } else {
      showToast("趋势数据更新失败，请到工作台日志查看原因。");
      setTrendUpdateStatus("更新失败，请返回工作台查看执行日志。");
    }
  } catch (error) {
    activeJobId = null;
    setUpdateButtonDisabled(false);
    setTrendUpdateStatus(error.message);
    showToast(error.message);
  }
}

async function updateTrendData() {
  if (activeJobId) {
    showToast("趋势更新正在运行，请等它完成。");
    return;
  }
  try {
    const task = await loadTrendUpdateTask();
    const weeks = await trendUpdateWeeks();
    const weekText = weeks.map((week) => `W${week}`).join("、");
    const ok = window.confirm(
      `将更新 ${weekText} 的完课和直播数据，完成后自动刷新每周趋势图。确认继续吗？`,
    );
    if (!ok) return;
    setUpdateButtonDisabled(true);
    setTrendUpdateStatus(`正在启动趋势更新任务：${weekText}…`);
    const data = await request("/api/run", {
      method: "POST",
      body: JSON.stringify({
        task_id: task.id,
        confirmed: true,
        weeks,
      }),
    });
    activeJobId = data.job_id;
    showToast("已开始更新趋势数据。");
    pollTrendUpdate(data.job_id);
  } catch (error) {
    activeJobId = null;
    setUpdateButtonDisabled(false);
    setTrendUpdateStatus(error.message);
    showToast(error.message);
  }
}

function renderLegend(series) {
  $("#trendLegend").innerHTML = (series || []).map((item) => `
    <span class="trend-legend-item">
      <i style="background:${escapeHtml(item.color)}"></i>${escapeHtml(item.label)}
    </span>
  `).join("");
}

function renderStats(points) {
  const latest = points.at(-1);
  if (!latest) {
    $("#trendStats").innerHTML = '<div class="empty-state">暂无趋势数据。先更新一次完课数据后再查看。</div>';
    $("#trendLatestWeek").textContent = "--";
    return;
  }
  $("#trendLatestWeek").textContent = `${latest.label} · ${latest.total} 人`;
  const stats = [
    ["完课率", latest.finished_rate, `${latest.finished} 人已完课`],
    ["未到课率", latest.absent_rate, `${latest.absent} 人未到课`],
    ["到课未完课率", latest.arrived_unfinished_rate, `${latest.arrived_unfinished} 人需跟进`],
    ["直播参与率", latest.live_rate, latest.live_rate === null ? "暂无直播缓存" : "来自直播缓存"],
  ];
  $("#trendStats").innerHTML = stats.map(([label, rate, note]) => `
    <article class="trend-stat">
      <span>${escapeHtml(label)}</span>
      <strong>${formatPercent(rate)}</strong>
      <small>${escapeHtml(note)}</small>
    </article>
  `).join("");
}

function renderTable(points) {
  if (!points.length) {
    $("#trendTable").innerHTML = '<div class="empty-state">暂无周数据明细</div>';
    return;
  }
  const detailButton = (point, metricKey, count, rate = null) => {
    const students = point.students_by_metric?.[metricKey] || [];
    const disabled = !students.length ? " disabled" : "";
    const title = students.length ? "点击查看具体学生" : "暂无可查看名单";
    const rateText = rate === null ? "" : `<span>${formatPercent(rate)}</span>`;
    return `
      <button class="trend-count-link" type="button" data-trend-week="${escapeHtml(point.week)}" data-trend-metric="${escapeHtml(metricKey)}"${disabled} title="${title}">
        <strong>${escapeHtml(count)}</strong>${rateText}
      </button>
    `;
  };
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
          <th>第一课未完课</th>
          <th>直播参与</th>
          <th>数据来源</th>
        </tr>
      </thead>
      <tbody>
        ${points.map((point) => `
          <tr>
            <td>${escapeHtml(point.label)}</td>
            <td>第${escapeHtml(point.courses[0])}-${escapeHtml(point.courses[1])}课</td>
            <td>${detailButton(point, "all", point.total)}</td>
            <td>${detailButton(point, "finished", point.finished, point.finished_rate)}</td>
            <td>${detailButton(point, "absent", point.absent, point.absent_rate)}</td>
            <td>${detailButton(point, "arrived_unfinished", point.arrived_unfinished, point.arrived_unfinished_rate)}</td>
            <td>${detailButton(point, "first_lesson_unfinished", point.first_lesson_unfinished, point.first_lesson_unfinished_rate)}</td>
            <td>${formatPercent(point.live_rate)}</td>
            <td><code>${escapeHtml(point.source)}</code></td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

function classTimeRank(value) {
  const text = String(value || "");
  if (text.includes("周五")) return 1;
  if (text.includes("周六午")) return 2;
  if (text.includes("周六晚")) return 3;
  if (text.includes("周六")) return 4;
  return 99;
}

function renderTrendClassTimeFilter() {
  const select = $("#trendClassTimeFilter");
  if (!select) return;
  const current = select.value;
  const classTimes = [...new Set(activeTrendStudents.map((student) => String(student.class_time || "未记录")))]
    .sort((left, right) => classTimeRank(left) - classTimeRank(right) || left.localeCompare(right, "zh-CN"));
  select.innerHTML = [
    '<option value="">全部上课时间</option>',
    ...classTimes.map((classTime) => `<option value="${escapeHtml(classTime)}">${escapeHtml(classTime)}</option>`),
  ].join("");
  select.value = classTimes.includes(current) ? current : "";
}

function filteredTrendStudents() {
  const needle = ($("#trendStudentSearch")?.value || "").trim().toLowerCase();
  const classTime = $("#trendClassTimeFilter")?.value || "";
  return activeTrendStudents.filter((student) => {
    const matchesSearch = !needle || `${student.name || ""} ${student.id || ""}`.toLowerCase().includes(needle);
    const matchesClassTime = !classTime || String(student.class_time || "未记录") === classTime;
    return matchesSearch && matchesClassTime;
  });
}

function renderTrendStudentRows() {
  const students = filteredTrendStudents();
  const rows = $("#trendStudentRows");
  if (!rows) return;
  rows.innerHTML = students.length
    ? students.map((student) => `
      <tr>
        <td>${escapeHtml(student.name || "未记录")}</td>
        <td>${escapeHtml(student.id || "")}</td>
        <td><span class="student-status">${escapeHtml(student.status || "未分类")}</span></td>
        <td>${escapeHtml(student.class_time || "未记录")}</td>
        <td>${escapeHtml(student.class_name || "未记录")}</td>
      </tr>
    `).join("")
    : '<tr><td colspan="5" class="no-results">没有匹配的学生</td></tr>';
  const copyButton = $("#copyTrendStudentIds");
  if (copyButton) {
    copyButton.textContent = students.length ? `复制当前 ${students.length} 个 ID` : "暂无可复制 ID";
    copyButton.disabled = !students.length;
  }
}

function openTrendDetail(week, metricKey) {
  const point = (trendData?.points || []).find((item) => Number(item.week) === Number(week));
  if (!point) return;
  const students = point.students_by_metric?.[metricKey] || [];
  activeTrendStudents = students;
  $("#trendDetailTitle").textContent = `${point.label} ${trendMetricLabels[metricKey] || "学生名单"}`;
  $("#trendDetailSummary").textContent = `第${point.courses?.[0] || "-"}-${point.courses?.[1] || "-"}课，共 ${students.length} 人。可搜索姓名或学生 ID，并复制当前筛选结果。`;
  $("#trendStudentSearch").value = "";
  if ($("#trendClassTimeFilter")) $("#trendClassTimeFilter").value = "";
  renderTrendClassTimeFilter();
  renderTrendStudentRows();
  $("#trendDetailDialog").showModal();
}

async function copyTrendStudentIds() {
  const students = filteredTrendStudents();
  if (!students.length) {
    showToast("当前没有可复制的学生 ID");
    return;
  }
  const text = students.map((student) => student.id).filter(Boolean).join("\n");
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
  }
  showToast(`已复制 ${students.length} 个学生 ID`);
}

function chartSeries(points, series) {
  return (series || [])
    .filter((item) => points.some((point) => point[item.key] !== null && point[item.key] !== undefined))
    .map((item) => ({
      name: item.label,
      type: "line",
      smooth: true,
      symbol: "circle",
      symbolSize: 9,
      showSymbol: true,
      connectNulls: false,
      data: points.map((point) => point[item.key]),
      lineStyle: { width: item.key === "finished_rate" ? 4 : 3, color: item.color },
      itemStyle: { color: item.color, borderColor: "#fffaf0", borderWidth: 2 },
      areaStyle: item.key === "finished_rate"
        ? {
            color: {
              type: "linear",
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: "rgba(54, 122, 75, 0.18)" },
                { offset: 1, color: "rgba(54, 122, 75, 0.02)" },
              ],
            },
          }
        : undefined,
    }));
}

function renderChart(points, series) {
  const container = $("#trendChart");
  if (!points.length) {
    container.innerHTML = '<div class="empty-state">暂无可用于趋势图的数据。先更新一次完课数据后再回来看看。</div>';
    return;
  }
  if (!window.echarts) {
    container.innerHTML = '<div class="empty-state">ECharts 没有加载成功，请重启教师工作台后刷新。</div>';
    return;
  }
  container.innerHTML = "";
  if (!trendChart) trendChart = echarts.init(container, null, { renderer: "canvas" });
  const labels = points.map((point) => `${point.label}\n第${point.courses[0]}-${point.courses[1]}课`);
  trendChart.setOption({
    color: (series || []).map((item) => item.color),
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis",
      backgroundColor: "rgba(24, 49, 40, 0.94)",
      borderWidth: 0,
      textStyle: { color: "#fffaf0", fontFamily: "MiSans, sans-serif" },
      valueFormatter: (value) => formatPercent(value),
      axisPointer: {
        type: "line",
        lineStyle: { color: "rgba(24, 49, 40, 0.20)", width: 1.5 },
      },
    },
    grid: { left: 58, right: 30, top: 42, bottom: 58, containLabel: true },
    xAxis: {
      type: "category",
      boundaryGap: false,
      data: labels,
      axisTick: { show: false },
      axisLine: { lineStyle: { color: "rgba(24, 49, 40, 0.18)" } },
      axisLabel: {
        color: "rgba(24, 35, 31, 0.66)",
        fontWeight: 800,
        lineHeight: 18,
      },
    },
    yAxis: {
      type: "value",
      min: 0,
      max: 100,
      interval: 20,
      axisLabel: {
        formatter: "{value}%",
        color: "rgba(24, 35, 31, 0.58)",
        fontWeight: 800,
      },
      splitLine: { lineStyle: { color: "rgba(24, 49, 40, 0.09)" } },
    },
    series: chartSeries(points, series),
  }, true);
}

function renderTrendData(data) {
  trendData = data;
  $("#trendCheckedAt").textContent = data.checked_at ? `检查于 ${data.checked_at.split(" ")[1] || data.checked_at}` : "已读取本地数据";
  $("#trendMessage").textContent = data.message || "已读取本地缓存。";
  renderStats(data.points || []);
  renderLegend(data.series || []);
  renderChart(data.points || [], data.series || []);
  renderTable(data.points || []);
}

async function loadTrends() {
  try {
    renderTrendData(await request("/api/trends"));
  } catch (error) {
    try {
      renderTrendData(await loadSummaryFallback(error));
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

window.addEventListener("resize", () => {
  if (trendChart) trendChart.resize();
});

$("#refreshTrends").addEventListener("click", loadTrends);
$("#updateTrendData").addEventListener("click", updateTrendData);
document.addEventListener("click", (event) => {
  const detailButton = event.target.closest("[data-trend-week][data-trend-metric]");
  if (!detailButton || detailButton.disabled) return;
  openTrendDetail(detailButton.dataset.trendWeek, detailButton.dataset.trendMetric);
});
$("#closeTrendDetails")?.addEventListener("click", () => $("#trendDetailDialog").close());
$("#trendStudentSearch")?.addEventListener("input", renderTrendStudentRows);
$("#trendClassTimeFilter")?.addEventListener("change", renderTrendStudentRows);
$("#copyTrendStudentIds")?.addEventListener("click", copyTrendStudentIds);
loadTrends();
loadTrendUpdateTask().catch(() => {});
