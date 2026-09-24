const $ = (selector) => document.querySelector(selector);
const state = { config: null, manifest: null, selected: new Set(), jobId: "" };

function escapeHtml(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function showToast(message) {
  const toast = $("#toast");
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2600);
}

async function request(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "请求失败");
  return data;
}

function selectedLessonCounts() {
  return [...document.querySelectorAll("input[name='ndLessonCount']:checked")].map((input) => input.value);
}

function syncLessonCountLabel() {
  const values = selectedLessonCounts();
  const label = $("#ndLessonCountLabel");
  if (!label) return;
  if (!values.length) {
    label.textContent = "未完课节数";
  } else if (values.length === 1) {
    label.textContent = values[0] === "5+" ? "5 节及以上" : `${values[0]} 节未完`;
  } else {
    label.textContent = `未完课节数（${values.length}）`;
  }
}

function visibleRows() {
  const search = $("#ndSearch").value.trim().toLowerCase();
  const status = $("#ndStatusFilter").value;
  const lessonCounts = selectedLessonCounts();
  return (state.manifest?.items || []).filter((row) => {
    const unfinishedCount = (row.unfinished || []).length;
    const matchesSearch = !search || `${row.name || ""} ${row.student_id || ""}`.toLowerCase().includes(search);
    const matchesStatus = !status
      || (status === "image" ? row.image_exists === true && row.sent !== true
        : status === "pending" ? row.image_exists !== true
          : row.sent === true);
    const matchesLessonCount = !lessonCounts.length || lessonCounts.some((value) => value === "5+" ? unfinishedCount >= 5 : unfinishedCount === Number(value));
    return matchesSearch && matchesStatus && matchesLessonCount;
  });
}

function renderStats() {
  const items = state.manifest?.items || [];
  const imageCount = items.filter((row) => row.image_exists === true).length;
  const sentCount = items.filter((row) => row.sent === true).length;
  const selectedImageCount = [...state.selected].filter((id) => items.some((row) => String(row.student_id) === id && row.image_exists === true && row.sent !== true)).length;
  const selectedGeneratedCount = [...state.selected].filter((id) => items.some((row) => String(row.student_id) === id && row.image_exists === true)).length;
  const selectedSentCount = [...state.selected].filter((id) => items.some((row) => String(row.student_id) === id && row.sent === true)).length;
  $("#ndStats").innerHTML = [
    ["未完课学员", items.length, "当前清单人数"],
    ["已生成图片", imageCount, "可创建企微待发送"],
    ["已创建待发送", sentCount, "仍需企微确认"],
    ["已选择", state.selected.size, `可删除 ${selectedGeneratedCount} · 可群发 ${selectedImageCount} · 可取消 ${selectedSentCount}`],
  ].map(([label, value, note]) => `<article class="monthly-stat"><span>${label}</span><strong>${value}</strong><small>${note}</small></article>`).join("");
  $("#ndHeroReady").textContent = `${items.length} 人`;
  $("#ndHeroMeta").textContent = state.manifest ? `图片 ${imageCount} · 已群发 ${sentCount}` : "尚未读取";
  $("#ndSelectedCount").textContent = `已选择 ${state.selected.size} 人 · 可删除 ${selectedGeneratedCount} · 可群发 ${selectedImageCount} · 可取消 ${selectedSentCount}`;
  $("#ndDeleteImages").disabled = selectedGeneratedCount === 0;
  $("#ndCancelSend").disabled = selectedSentCount === 0;
  $("#ndSendSelected").disabled = selectedImageCount === 0;
}

function renderRows() {
  const visible = visibleRows();
  $("#ndRows").innerHTML = visible.length ? visible.map((row) => {
    const id = String(row.student_id);
    const checked = state.selected.has(id) ? "checked" : "";
    const status = row.sent
      ? `<span class="row-status sent">已创建待发送</span>`
      : row.image_exists
        ? `<span class="row-status ready">已生成图片</span>`
        : `<span class="row-status blocked">未生成图片</span>`;
    const days = (row.days || []).map((item, index) => `<small>${10}.${index + 1}：${escapeHtml(item)}</small>`).join("");
    const shortLessons = (row.unfinished || []).slice(0, 3).join("、") + ((row.unfinished || []).length > 3 ? "…" : "");
    return `<tr><td class="check-col"><input type="checkbox" data-nd-select="${escapeHtml(id)}" ${checked} aria-label="选择 ${escapeHtml(row.name)}"></td><td><strong>${escapeHtml(row.name || "未录姓名")}</strong><small class="data-mono">${escapeHtml(id)}</small></td><td><strong>${(row.unfinished || []).length} 节</strong><small>${escapeHtml(shortLessons)}</small></td><td class="nd-days-cell">${days}</td><td>${status}</td></tr>`;
  }).join("") : '<tr><td colspan="5" class="empty-cell">当前筛选没有学员。</td></tr>';
  const visibleIds = visible.map((row) => String(row.student_id));
  $("#ndSelectAll").checked = visibleIds.length > 0 && visibleIds.every((id) => state.selected.has(id));
  $("#ndSelectAll").indeterminate = visibleIds.some((id) => state.selected.has(id)) && !$("#ndSelectAll").checked;
  renderStats();
}

function keepValidSelection() {
  const validIds = new Set((state.manifest?.items || []).map((row) => String(row.student_id)));
  state.selected = new Set([...state.selected].filter((id) => validIds.has(id)));
}

async function refreshStatus() {
  const data = await request("/api/national-day");
  state.config = data.config || state.config;
  $("#ndMessageTemplate").value = state.config?.message || "";
  state.manifest = data.manifest;
  keepValidSelection();
  renderRows();
  $("#ndStatus").textContent = state.manifest ? "已加载上次国庆补课清单。" : "点击“刷新清单”读取未完课学员。";
}

async function saveMessage() {
  const message = $("#ndMessageTemplate").value.trim();
  if (!message) return showToast("家长话术不能为空");
  try {
    const data = await request("/api/national-day/config", { method: "POST", body: JSON.stringify({ message }) });
    state.config = data.config;
    $("#ndMessageTemplate").value = state.config?.message || message;
    showToast("国庆补课话术已保存");
  } catch (error) {
    showToast(error.message);
  }
}

async function preview() {
  $("#ndStatus").textContent = "正在从 CRM 更新最新完课，并生成清单…";
  try {
    const data = await request("/api/national-day/preview", { method: "POST", body: JSON.stringify({}) });
    state.config = data.config || state.config;
    state.manifest = data.manifest;
    keepValidSelection();
    renderRows();
    $("#ndStatus").textContent = `已从 CRM 更新完课，并读取 ${state.manifest?.count || 0} 名有偶数未完课的学员。`;
    showToast("国庆补课清单已更新");
  } catch (error) {
    $("#ndStatus").textContent = error.message;
    showToast(error.message);
  }
}

async function pollJob() {
  if (!state.jobId) return;
  try {
    const job = await request(`/api/jobs/${state.jobId}`);
    const last = (job.logs || []).slice(-5).join("\n");
    $("#ndJobStatus").hidden = false;
    $("#ndJobStatus").textContent = `${job.status === "success" ? "已完成" : job.status === "failed" ? "执行失败" : "正在执行"} · ${last}`;
    if (["queued", "running", "stopping"].includes(job.status)) {
      setTimeout(pollJob, 1200);
      return;
    }
    state.jobId = "";
    await refreshStatus();
    showToast(job.status === "success" ? "国庆补课任务已完成" : "任务已结束，请查看运行记录");
  } catch (error) {
    $("#ndJobStatus").textContent = error.message;
  }
}

async function generateSelected() {
  const ids = [...state.selected];
  if (!ids.length) return showToast("请先选择学员");
  try {
    const data = await request("/api/national-day/generate", { method: "POST", body: JSON.stringify({ student_ids: ids }) });
    state.jobId = data.job_id;
    $("#ndJobStatus").hidden = false;
    $("#ndJobStatus").textContent = `已开始生成 ${data.selected_count} 张补课计划图片…`;
    showToast("已开始生成国庆补课图片");
    pollJob();
  } catch (error) {
    showToast(error.message);
  }
}

async function sendSelected() {
  const ids = [...state.selected].filter((id) => (state.manifest?.items || []).some((row) => String(row.student_id) === id && row.image_exists === true && row.sent !== true));
  if (!ids.length) return showToast("请先选择已生成图片的学员");
  if (!window.confirm(`确认为 ${ids.length} 名学员创建企微待发送任务吗？最终发送仍需在企微客户端确认。`)) return;
  try {
    const data = await request("/api/national-day/send", { method: "POST", body: JSON.stringify({ student_ids: ids, confirmed: true }) });
    state.jobId = data.job_id;
    $("#ndJobStatus").hidden = false;
    $("#ndJobStatus").textContent = `已开始创建 ${data.selected_count} 个企微待发送任务…`;
    showToast("已开始创建企微待发送任务");
    pollJob();
  } catch (error) {
    showToast(error.message);
  }
}

async function cancelSelectedSend() {
  const ids = [...state.selected].filter((id) => (state.manifest?.items || []).some((row) => String(row.student_id) === id && row.sent === true));
  if (!ids.length) return showToast("请先选择已创建待发送的学员");
  if (!window.confirm(`确认取消 ${ids.length} 名学员的国庆补课待发送任务吗？如果已经在企微客户端确认发送，CRM 会拒绝取消。`)) return;
  try {
    const data = await request("/api/national-day/cancel", { method: "POST", body: JSON.stringify({ student_ids: ids, confirmed: true }) });
    state.jobId = data.job_id;
    $("#ndJobStatus").hidden = false;
    $("#ndJobStatus").textContent = `已开始取消 ${data.selected_count} 个国庆补课待发送任务…`;
    showToast(data.skipped_not_sent?.length ? `已跳过 ${data.skipped_not_sent.length} 个未群发学员，其余开始取消` : "已开始取消国庆补课群发");
    pollJob();
  } catch (error) {
    showToast(error.message);
  }
}

async function deleteSelectedImages() {
  const ids = [...state.selected].filter((id) => (state.manifest?.items || []).some((row) => String(row.student_id) === id && row.image_exists === true));
  if (!ids.length) return showToast("请先选择已生成图片的学员");
  if (!window.confirm(`确认删除 ${ids.length} 名学员已生成的补课计划图片吗？清单会保留，可重新生成。`)) return;
  try {
    const data = await request("/api/national-day/delete-images", { method: "POST", body: JSON.stringify({ student_ids: ids, confirmed: true }) });
    state.manifest = data.manifest;
    keepValidSelection();
    renderRows();
    showToast(`已删除 ${data.deleted_count || 0} 张补课计划图片`);
  } catch (error) {
    showToast(error.message);
  }
}

$("#ndSaveMessage").addEventListener("click", saveMessage);
$("#ndPreview").addEventListener("click", preview);
$("#ndGenerateVisible").addEventListener("click", generateSelected);
$("#ndDeleteImages").addEventListener("click", deleteSelectedImages);
$("#ndCancelSend").addEventListener("click", cancelSelectedSend);
$("#ndSendSelected").addEventListener("click", sendSelected);
$("#ndSearch").addEventListener("input", renderRows);
$("#ndStatusFilter").addEventListener("change", renderRows);

const lessonCountToggle = $("#ndLessonCountToggle");
const lessonCountMenu = $("#ndLessonCountMenu");
lessonCountToggle.addEventListener("click", () => {
  const open = lessonCountMenu.hidden;
  lessonCountMenu.hidden = !open;
  lessonCountToggle.setAttribute("aria-expanded", open ? "true" : "false");
});
lessonCountMenu.addEventListener("change", (event) => {
  if (!event.target.matches("input[name='ndLessonCount']")) return;
  syncLessonCountLabel();
  renderRows();
});
document.addEventListener("click", (event) => {
  if (!event.target.closest("#ndLessonCountFilter")) {
    lessonCountMenu.hidden = true;
    lessonCountToggle.setAttribute("aria-expanded", "false");
  }
});
syncLessonCountLabel();
$("#ndSelectVisible").addEventListener("click", () => { visibleRows().forEach((row) => state.selected.add(String(row.student_id))); renderRows(); });
$("#ndClearSelection").addEventListener("click", () => { state.selected.clear(); renderRows(); });
$("#ndSelectAll").addEventListener("change", (event) => { visibleRows().forEach((row) => event.target.checked ? state.selected.add(String(row.student_id)) : state.selected.delete(String(row.student_id))); renderRows(); });
$("#ndRows").addEventListener("change", (event) => {
  const input = event.target.closest("[data-nd-select]");
  if (!input) return;
  input.checked ? state.selected.add(input.dataset.ndSelect) : state.selected.delete(input.dataset.ndSelect);
  renderStats();
});

refreshStatus().catch((error) => { $("#ndStatus").textContent = error.message; showToast(error.message); });
