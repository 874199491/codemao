const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const BANDS = ["0-69", "70-79", "80-89", "90-99", "100"];
const state = { config: null, manifest: null, selected: new Set(), activeBand: "0-69", bandSelect: null, readySelect: null, jobId: "" };

function escapeHtml(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}
function showToast(message) { const node = $("#toast"); node.textContent = message; node.classList.add("show"); setTimeout(() => node.classList.remove("show"), 2800); }
async function request(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const text = await response.text(); let payload;
  try { payload = JSON.parse(text); } catch { throw new Error("接口返回不是有效 JSON，请重启教师工作台后再试。"); }
  if (!response.ok) throw new Error(payload.error || `请求失败：${response.status}`);
  return payload;
}
function formatScore(score) { return score === null || score === undefined || score === "" ? "--" : (Number(score) % 1 ? score : Number(score).toFixed(0)); }
function currentConfig() {
  if (!state.config) return null;
  state.config.templates[state.activeBand] = $("#templateText").value.trim();
  return {
    ...state.config,
    source_dir: $("[name='source_dir']").value.trim(), score_file: $("[name='score_file']").value.trim(),
    roster_json: $("[name='roster_json']").value.trim(), teacher_name: $("[name='teacher_name']").value.trim(),
    send_wrong_report: $("[name='send_wrong_report']").checked, send_award: $("[name='send_award']").checked,
    award_threshold: Number.isFinite(Number($("[name='award_threshold']").value)) ? Number($("[name='award_threshold']").value) : 80,
    protective_score_enabled: $("[name='protective_score_enabled']").checked,
    templates: { ...state.config.templates },
  };
}
function fillConfig(config) {
  state.config = { ...config, templates: { ...(config.templates || {}) } };
  $("[name='source_dir']").value = state.config.source_dir || "";
  $("[name='score_file']").value = state.config.score_file || "";
  $("[name='roster_json']").value = state.config.roster_json || "";
  $("[name='teacher_name']").value = state.config.teacher_name || "";
  $("[name='send_wrong_report']").checked = state.config.send_wrong_report !== false;
  $("[name='send_award']").checked = state.config.send_award !== false;
  $("[name='award_threshold']").value = state.config.award_threshold ?? 80;
  $("[name='protective_score_enabled']").checked = state.config.protective_score_enabled === true;
  renderTemplateTabs();
}
function renderTemplateTabs() {
  $("#templateTabs").innerHTML = BANDS.map((band) => `<button type="button" role="tab" class="template-tab ${band === state.activeBand ? "active" : ""}" data-template-band="${band}">${band === "100" ? "100分" : `${band}分`}</button>`).join("");
  $("#templateBandHint").textContent = state.activeBand === "100" ? "100 分" : `${state.activeBand} 分`;
  $("#templateText").value = state.config?.templates?.[state.activeBand] || "";
}
function collectVisibleRows() {
  const manifest = state.manifest || {};
  const search = $("#monthlySearch").value.trim().toLowerCase();
  const selectedBands = [...$("#monthlyBandFilter").selectedOptions].map((option) => option.value).filter(Boolean);
  const ready = $("#monthlyReadyFilter").value;
  return (manifest.students || []).filter((row) => {
    const matchesSearch = !search || `${row.student_name || ""} ${row.student_id || ""}`.toLowerCase().includes(search);
    const matchesBand = !selectedBands.length || selectedBands.includes(row.band);
    const matchesReady = !ready
      || (ready === "ready" ? row.send_ready === true && row.sent !== true
        : ready === "sent" ? row.sent === true
          : row.send_ready !== true);
    return matchesSearch && matchesBand && matchesReady;
  });
}
function renderStats() {
  const manifest = state.manifest || {};
  const sentCount = manifest.sent_count || (manifest.students || []).filter((row) => row.sent === true).length;
  const readyUnsentCount = manifest.ready_unsent_count ?? (manifest.students || []).filter((row) => row.send_ready === true && row.sent !== true).length;
  const adjustedCount = manifest.adjusted_score_count || (manifest.students || []).filter((row) => row.display_score_adjusted === true).length;
  const selectedReady = [...state.selected].filter((id) => (manifest.students || []).some((row) => String(row.student_id) === id && row.send_ready === true && row.sent !== true)).length;
  const selectedSent = [...state.selected].filter((id) => (manifest.students || []).some((row) => String(row.student_id) === id && row.sent === true)).length;
  $("#monthlyStats").innerHTML = [
    ["成绩记录", manifest.student_count || 0, "从当前成绩文件识别"],
    ["可发送", readyUnsentCount, "未发送且校验通过"],
    ["已发送", sentCount, "已创建企微待发送任务"],
    ["展示分", adjustedCount, "仅影响反馈预览话术"],
    ["需处理", manifest.blocked_count || 0, "不发送或缺少校验项"],
    ["已选择", selectedReady, "本次准备创建任务"],
  ].map(([label, value, note]) => `<article class="monthly-stat"><span>${label}</span><strong>${value}</strong><small>${note}</small></article>`).join("");
  $("#heroReady").textContent = `${readyUnsentCount} 人可发送`;
  $("#heroMeta").textContent = manifest.score_workbook ? `成绩文件：${manifest.score_workbook.split(/[\\/]/).pop()}` : "尚未生成预览";
  $("#selectedMonthlyCount").textContent = `已选择 ${state.selected.size} 人 · 可发送 ${selectedReady} · 可取消 ${selectedSent}`;
  $("#sendMonthlyExam").disabled = selectedReady === 0;
  $("#cancelMonthlyExam").disabled = selectedSent === 0;
}
function renderRows() {
  const rows = collectVisibleRows();
  $("#monthlyRows").innerHTML = rows.length ? rows.map((row) => {
    const id = escapeHtml(row.student_id);
    const sendable = row.send_ready === true && row.sent !== true;
    const actionable = sendable || row.sent === true;
    const checked = state.selected.has(String(row.student_id)) && actionable ? "checked" : "";
    const blockers = (row.blockers || []).map(escapeHtml).join("；");
    const attachments = [row.pdf ? "错题 PDF" : "", row.award ? "奖状 PNG" : ""].filter(Boolean).join(" · ") || "无附件";
    const scoreNote = row.display_score_adjusted ? `<small class="score-adjusted-note">${escapeHtml(row.score_adjustment_note || `原始 ${formatScore(row.original_score)} → 展示 ${formatScore(row.score)}`)}</small>` : "";
    const statusCell = row.sent
      ? `<span class="row-status sent">已发送</span><small>${escapeHtml(row.sent_at || "已创建待发送任务")}</small>`
      : row.send_ready
        ? `<span class="row-status ready">可发送</span>`
        : `<span class="row-status blocked">需处理</span><small class="row-blockers">${blockers}</small>`;
    return `<tr class="${row.sent ? "is-sent" : row.send_ready ? "" : "is-blocked"}"><td class="check-col"><input type="checkbox" data-monthly-select="${id}" ${checked} ${actionable ? "" : "disabled"} aria-label="选择 ${escapeHtml(row.student_name)}"></td><td><strong>${escapeHtml(row.student_name || "未录姓名")}</strong><small class="data-mono">${id}</small></td><td><strong>${escapeHtml(formatScore(row.score))}</strong>${scoreNote}<span class="band-chip band-${escapeHtml(row.band || "none")}">${escapeHtml(row.band || "未分档")}</span></td><td>${row.wrong_count ? `<span class="wrong-count">${row.wrong_count} 题</span><small>第 ${escapeHtml((row.wrong_questions || []).join("、"))}</small>` : "无错题"}</td><td><span class="attachment-status">${escapeHtml(attachments)}</span></td><td>${statusCell}</td><td><button class="text-button row-preview" type="button" data-monthly-preview="${id}">查看话术</button></td></tr>`;
  }).join("") : '<tr><td colspan="7" class="empty-cell">当前筛选没有学员。</td></tr>';
  const visibleActionable = rows.filter((row) => row.sent === true || (row.send_ready && row.sent !== true)).map((row) => String(row.student_id));
  $("#selectAllMonthly").checked = visibleActionable.length > 0 && visibleActionable.every((id) => state.selected.has(id));
  $("#selectAllMonthly").indeterminate = visibleActionable.some((id) => state.selected.has(id)) && !$("#selectAllMonthly").checked;
  renderStats();
}
function renderFilters() {
  const band = $("#monthlyBandFilter");
  const selectedBands = new Set([...band.selectedOptions].map((option) => option.value).filter(Boolean));
  band.innerHTML = BANDS.map((item) => `<option value="${item}" ${selectedBands.has(item) ? "selected" : ""}>${item === "100" ? "100 分" : `${item} 分`}</option>`).join("");
  state.bandSelect?.destroy();
  state.readySelect?.destroy();
  state.bandSelect = new SlimSelect({ select: "#monthlyBandFilter", settings: { showSearch: false, allowDeselect: true, closeOnSelect: false, placeholderText: "全部成绩档位" } });
  state.readySelect = new SlimSelect({ select: "#monthlyReadyFilter", settings: { showSearch: false, allowDeselect: true } });
}
function showPreview(row) {
  $("#dialogStudentName").textContent = row.student_name || "反馈预览";
  $("#dialogStudentMeta").textContent = `${row.student_id} · ${formatScore(row.score)} 分 · ${row.band || "未分档"}`;
  $("#dialogMessage").textContent = row.message || "暂无话术";
  const attachments = [row.pdf ? `错题报告：${row.pdf.split(/[\\/]/).pop()}` : "", row.award ? `奖状：${row.award.split(/[\\/]/).pop()}` : ""].filter(Boolean);
  $("#dialogAttachments").innerHTML = attachments.length ? attachments.map((item) => `<span>${escapeHtml(item)}</span>`).join("") : "<span>本条没有附件</span>";
  $("#monthlyPreviewDialog").hidden = false;
}
async function saveConfig(showMessage = true) {
  const config = currentConfig();
  const data = await request("/api/monthly-exam/config", { method: "POST", body: JSON.stringify(config) });
  fillConfig(data.config);
  if (showMessage) showToast("月考反馈配置已保存");
}
async function generatePreview() {
  $("#configStatus").textContent = "正在读取成绩表并匹配附件…";
  try {
    const config = currentConfig();
    const data = await request("/api/monthly-exam/preview", { method: "POST", body: JSON.stringify({ config }) });
    fillConfig(data.config); state.manifest = data.manifest;
    const validIds = new Set((state.manifest.students || []).filter((row) => row.sent === true || (row.send_ready && row.sent !== true)).map((row) => String(row.student_id)));
    state.selected = new Set([...state.selected].filter((id) => validIds.has(id)));
    renderFilters(); renderRows();
    $("#configStatus").textContent = `已生成 ${data.manifest.student_count || 0} 条预览，其中 ${data.manifest.ready_unsent_count ?? data.manifest.ready_count ?? 0} 条可发送，${data.manifest.sent_count || 0} 条已发送。`;
    showToast("月考反馈预览已更新");
  } catch (error) { $("#configStatus").textContent = error.message; showToast(error.message); }
}
async function sendSelected() {
  const ids = [...state.selected].filter((id) => (state.manifest?.students || []).some((row) => String(row.student_id) === id && row.send_ready && row.sent !== true));
  if (!ids.length) return showToast("请先选择可发送学员");
  if (!window.confirm(`确认创建 ${ids.length} 名学员的企微待发送任务吗？系统会重新预览并逐个校验；最终发送仍需在企微客户端确认。`)) return;
  try {
    const data = await request("/api/monthly-exam/send", { method: "POST", body: JSON.stringify({ student_ids: ids, confirmed: true }) });
    state.jobId = data.job_id; $("#monthlyJobStatus").hidden = false; $("#monthlyJobStatus").textContent = `已开始创建 ${data.selected_count} 个待发送任务…`;
    showToast(data.skipped_sent?.length ? `已跳过 ${data.skipped_sent.length} 个已发送学员，其余任务已开始创建` : "已开始创建企微待发送任务"); pollJob();
  } catch (error) { showToast(error.message); }
}
async function sendAllReady() {
  const ids = (state.manifest?.students || []).filter((row) => row.send_ready && row.sent !== true).map((row) => String(row.student_id));
  if (!ids.length) return showToast("当前没有可发送学员");
  if (!window.confirm(`将一键为全部 ${ids.length} 名可发送学员创建企微待发送任务（错题报告/奖状会一并上传）。系统会重新预览并逐个校验；最终发送仍需在企微客户端确认。`)) return;
  try {
    const data = await request("/api/monthly-exam/send", { method: "POST", body: JSON.stringify({ student_ids: ids, confirmed: true }) });
    state.jobId = data.job_id; $("#monthlyJobStatus").hidden = false; $("#monthlyJobStatus").textContent = `已开始为 ${data.selected_count} 名学员创建待发送任务…`;
    showToast("已开始创建企微待发送任务"); pollJob();
  } catch (error) { showToast(error.message); }
}
async function cancelSelected() {
  const ids = [...state.selected].filter((id) => (state.manifest?.students || []).some((row) => String(row.student_id) === id && row.sent === true));
  if (!ids.length) return showToast("请先选择已发送学员");
  if (!window.confirm(`确认取消 ${ids.length} 名学员的月考反馈待发送任务吗？只会取消月考反馈记录；取消成功后会恢复为可发送。`)) return;
  try {
    const data = await request("/api/monthly-exam/cancel", { method: "POST", body: JSON.stringify({ student_ids: ids, confirmed: true }) });
    state.jobId = data.job_id;
    $("#monthlyJobStatus").hidden = false;
    $("#monthlyJobStatus").textContent = `已开始取消 ${data.selected_count} 个待发送任务…`;
    showToast(data.skipped_not_sent?.length ? `已跳过 ${data.skipped_not_sent.length} 个未发送学员，其余开始取消` : "已开始取消月考反馈");
    pollJob();
  } catch (error) { showToast(error.message); }
}
async function generateMaterials() {
  const threshold = state.config?.award_threshold ?? 80;
  if (!window.confirm(`将启动桌面「月考反馈助手」批量生成错题解析报告和奖状（奖状阈值：${threshold} 分，约 20-40 分钟）。请先关闭正在运行的「月考反馈助手」窗口，避免文件冲突。生成完成后点「刷新预览」即可匹配附件。继续吗？`)) return;
  try {
    const data = await request("/api/monthly-exam/generate", { method: "POST", body: JSON.stringify({ confirmed: true }) });
    state.jobId = data.job_id;
    $("#monthlyJobStatus").hidden = false;
    $("#monthlyJobStatus").textContent = "已开始生成错题报告与奖状，日志实时显示中…";
    showToast("已开始生成月考反馈物料");
    pollJob();
  } catch (error) { showToast(error.message); }
}
async function refreshMonthlyExamStatus() {
  const data = await request("/api/monthly-exam");
  fillConfig(data.config); state.manifest = data.manifest;
  const validIds = new Set((state.manifest?.students || []).filter((row) => row.sent === true || (row.send_ready && row.sent !== true)).map((row) => String(row.student_id)));
  state.selected = new Set([...state.selected].filter((id) => validIds.has(id)));
  renderFilters(); renderRows();
}
async function pollJob() {
  if (!state.jobId) return;
  try {
    const job = await request(`/api/jobs/${state.jobId}`);
    const last = (job.logs || []).slice(-4).join("\n");
    $("#monthlyJobStatus").textContent = `${job.status === "success" ? "已完成" : job.status === "failed" ? "执行失败" : "正在执行"} · ${last}`;
    if (["running", "stopping"].includes(job.status)) setTimeout(pollJob, 1200);
    else {
      const success = job.status === "success";
      state.jobId = "";
      try { await refreshMonthlyExamStatus(); } catch {}
      showToast(success ? "月考反馈待发送任务已创建，已更新发送标记" : "月考反馈任务已结束，请查看运行记录");
    }
  } catch (error) { $("#monthlyJobStatus").textContent = error.message; }
}

$("#templateTabs").addEventListener("click", (event) => {
  const button = event.target.closest("[data-template-band]"); if (!button) return;
  state.config.templates[state.activeBand] = $("#templateText").value.trim(); state.activeBand = button.dataset.templateBand; renderTemplateTabs();
});
$("#saveMonthlyConfig").addEventListener("click", () => saveConfig().catch((error) => showToast(error.message)));
$("#previewMonthlyExam").addEventListener("click", generatePreview);
$("#refreshMonthlyExam").addEventListener("click", generatePreview);
$("#generateMonthlyMaterials").addEventListener("click", generateMaterials);
$("#monthlySearch").addEventListener("input", renderRows);
$("#monthlyBandFilter").addEventListener("change", renderRows);
$("#monthlyReadyFilter").addEventListener("change", renderRows);
$("#selectAllMonthly").addEventListener("change", (event) => {
  const visible = collectVisibleRows().filter((row) => row.sent === true || (row.send_ready && row.sent !== true)).map((row) => String(row.student_id));
  visible.forEach((id) => event.target.checked ? state.selected.add(id) : state.selected.delete(id)); renderRows();
});
$("#selectVisibleMonthly").addEventListener("click", () => { collectVisibleRows().filter((row) => row.sent === true || (row.send_ready && row.sent !== true)).forEach((row) => state.selected.add(String(row.student_id))); renderRows(); });
$("#clearMonthlySelection").addEventListener("click", () => { state.selected.clear(); renderRows(); });
$("#monthlyRows").addEventListener("change", (event) => { const input = event.target.closest("[data-monthly-select]"); if (!input) return; input.checked ? state.selected.add(input.dataset.monthlySelect) : state.selected.delete(input.dataset.monthlySelect); renderStats(); });
$("#monthlyRows").addEventListener("click", (event) => { const button = event.target.closest("[data-monthly-preview]"); if (!button) return; const row = (state.manifest?.students || []).find((item) => String(item.student_id) === button.dataset.monthlyPreview); if (row) showPreview(row); });
$("#sendMonthlyExam").addEventListener("click", sendSelected);
$("#sendAllMonthlyExam").addEventListener("click", sendAllReady);
$("#cancelMonthlyExam").addEventListener("click", cancelSelected);
$("#closeMonthlyPreview").addEventListener("click", () => { $("#monthlyPreviewDialog").hidden = true; });
$("#monthlyPreviewDialog").addEventListener("click", (event) => { if (event.target.id === "monthlyPreviewDialog") $("#monthlyPreviewDialog").hidden = true; });

request("/api/monthly-exam").then((data) => {
  fillConfig(data.config); state.manifest = data.manifest;
  renderFilters(); renderRows();
  if (state.manifest) $("#configStatus").textContent = "已加载上次预览；修改配置后请重新生成。";
}).catch((error) => { $("#configStatus").textContent = error.message; showToast(error.message); });
