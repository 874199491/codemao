const $ = (selector) => document.querySelector(selector);
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

async function runDetection(isRefresh = false) {
  const classCode = $("[name='pqClassCode']").value.trim();
  const sinceDays = Number($("[name='pqDays']").value || 0);
  $("#pqStatus").textContent = "正在检测未回复家长…";
  if (!isRefresh) $("#pqCount").textContent = "…";
  try {
    const data = await request("/api/monthly-exam/unreplied", { method: "POST", body: JSON.stringify({ class_code: classCode, since_days: sinceDays }) });
    const rows = data.students || [];
    $("#pqStatus").textContent = `检测完成：${rows.length} 个未回复（家长发了最新消息、老师未回）${classCode ? ` · 班级 ${classCode}` : ""}${sinceDays ? ` · 最近${sinceDays}天` : " · 全部"}`;
    $("#pqCount").textContent = String(rows.length);
    const tbody = $("#pqRows");
    if (!rows.length) {
      tbody.innerHTML = `<tr><td colspan="5" class="empty-cell">没有未回复家长</td></tr>`;
    } else {
      tbody.innerHTML = rows.map((r) => `<tr><td class="data-mono">${escapeHtml(r.parent_last_msg_at || "")}</td><td>${escapeHtml(r.teacher || "")}</td><td>${escapeHtml(r.parent_wechat || "")}</td><td class="data-mono">${escapeHtml(r.student_id || "")}</td><td>${r.total_msgs || 0}/${r.parent_msgs || 0}/${r.teacher_msgs || 0}</td></tr>`).join("");
    }
  } catch (error) { $("#pqStatus").textContent = error.message; }
}

$("#pqRun").addEventListener("click", () => runDetection(false));
$("#pqRefresh").addEventListener("click", () => runDetection(false));
