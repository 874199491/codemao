const $ = (selector) => document.querySelector(selector);
let performanceData = null;
let monthPicker = null;
let classTimeSelect = null;
let completedLessonsSelect = null;

async function request(path) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" } });
  const text = await response.text();
  let payload;
  try { payload = JSON.parse(text); } catch { throw new Error("绩效接口返回异常，请重启教师工作台后再试。"); }
  if (!response.ok) throw new Error(payload.error || `请求失败：${response.status}`);
  return payload;
}
function escapeHtml(value) { return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;"); }
function showToast(message) { const node = $("#toast"); node.textContent = message; node.classList.add("show"); setTimeout(() => node.classList.remove("show"), 2600); }
function monthValue(date = new Date()) { return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`; }
function formatStatus(status) { return status === "已完课" ? '<span class="performance-ok">已完课</span>' : `<span class="performance-pending">${escapeHtml(status || "未返回")}</span>`; }

function render(data) {
  performanceData = data;
  const period = data.period || {};
  $("#performancePeriod").textContent = `${period.start || "--"} → ${period.end || "--"}`;
  $("#performanceCheckedAt").textContent = `更新于 ${data.checked_at || "--"}`;
  $("#performanceStats").innerHTML = [
    ["统计学员", data.total_students || 0, "纳入当前绩效周期的有效学员"],
    ["应完成偶数课", data.expected_cells || 0, `${(data.lessons || []).length} 节 × ${data.total_students || 0} 人`],
    ["已完成偶数课", data.completed_cells || 0, "状态为已完课的课程单元"],
    ["整体完成率", `${data.completion_rate || 0}%`, "按课程单元计算"],
  ].map(([label, value, note]) => `<article class="performance-stat"><span>${label}</span><strong>${value}</strong><small>${note}</small></article>`).join("");
  $("#performanceLessons").innerHTML = (data.lessons || []).map((item) => `<div class="performance-lesson"><strong>第${item.lesson}课</strong><span>W${item.week} · ${item.date}</span></div>`).join("") || '<div class="empty-state">当前周期没有已缓存的偶数课数据。</div>';
  const times = [...new Set((data.students || []).map((row) => row.class_time).filter(Boolean))];
  classTimeSelect?.destroy();
  completedLessonsSelect?.destroy();
  $("#performanceClassTime").innerHTML = '<option value="">全部上课时间</option>' + times.map((time) => `<option value="${escapeHtml(time)}">${escapeHtml(time)}</option>`).join("");
  const lessonCount = (data.lessons || []).length;
  $("#performanceCompletedLessons").innerHTML = '<option value="">全部完成课节数</option>' + Array.from({ length: lessonCount + 1 }, (_, count) => `<option value="${count}">${count} 节</option>`).join("");
  classTimeSelect = new SlimSelect({ select: "#performanceClassTime", settings: { showSearch: false, allowDeselect: true } });
  completedLessonsSelect = new SlimSelect({ select: "#performanceCompletedLessons", settings: { showSearch: false, allowDeselect: true } });
  renderRows();
}
function renderRows() {
  const filter = $("#performanceClassTime").value;
  const completedFilter = $("#performanceCompletedLessons").value;
  const rows = (performanceData?.students || [])
    .filter((row) => (!filter || row.class_time === filter) && (completedFilter === "" || Number(row.completed) === Number(completedFilter)))
    .sort((a, b) => Number(a.completed || 0) - Number(b.completed || 0) || String(a.student_name || "").localeCompare(String(b.student_name || ""), "zh-CN"));
  $("#performanceRows").innerHTML = rows.map((row) => `<tr><td>${escapeHtml(row.student_name || "未录姓名")}</td><td><button class="id-copy data-mono" type="button" data-student-id="${escapeHtml(row.student_id)}" title="复制学生 ID">${escapeHtml(row.student_id)}</button></td><td>${escapeHtml(row.class_time || "未记录")}</td><td>${row.completed}/${row.expected}</td><td><strong>${row.rate}%</strong></td><td>${(row.lessons || []).map((lesson) => `第${lesson.lesson}课 ${formatStatus(lesson.status)}`).join(" · ")}</td></tr>`).join("") || '<tr><td colspan="6" class="empty-cell">没有符合条件的学员。</td></tr>';
}
async function copyText(value) {
  try { await navigator.clipboard.writeText(value); }
  catch {
    const node = document.createElement("textarea"); node.value = value; node.style.position = "fixed"; node.style.opacity = "0";
    document.body.appendChild(node); node.select(); document.execCommand("copy"); node.remove();
  }
}
function visibleRows() {
  const filter = $("#performanceClassTime").value;
  const completedFilter = $("#performanceCompletedLessons").value;
  return (performanceData?.students || []).filter((row) => (!filter || row.class_time === filter) && (completedFilter === "" || Number(row.completed) === Number(completedFilter)));
}
async function load() {
  try {
    const value = $("#performanceMonth").value || monthValue();
    const [year, month] = value.split("-");
    render(await request(`/api/performance?year=${year}&month=${month}`));
  } catch (error) { showToast(error.message); $("#performanceStats").innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`; }
}
monthPicker = flatpickr("#performanceMonth", {
  dateFormat: "Y-m",
  altInput: true,
  altFormat: "Y年m月",
  allowInput: false,
  locale: flatpickr.l10ns.zh,
  plugins: [new monthSelectPlugin({ shorthand: false, dateFormat: "Y-m", altFormat: "Y年m月" })],
  defaultDate: monthValue(),
  onChange: load,
});
$("#refreshPerformance").addEventListener("click", load);
$("#performanceClassTime").addEventListener("change", renderRows);
$("#performanceCompletedLessons").addEventListener("change", renderRows);
$("#copyPerformanceIds").addEventListener("click", async () => {
  const ids = visibleRows().map((row) => row.student_id).filter(Boolean);
  if (!ids.length) return showToast("当前筛选没有学员 ID");
  await copyText(ids.join("\n"));
  showToast(`已复制 ${ids.length} 个学员 ID`);
});
$("#performanceRows").addEventListener("click", async (event) => {
  const button = event.target.closest(".id-copy");
  if (!button) return;
  await copyText(button.dataset.studentId || button.textContent.trim());
  showToast(`已复制 ${button.dataset.studentId}`);
});
load();
