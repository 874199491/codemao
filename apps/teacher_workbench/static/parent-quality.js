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

async function runDetection(refreshData = false) {
  const sinceDays = 2; // 固定最近两天，与数据源抓取范围一致
  const buttons = [$("#pqRun"), $("#pqRefresh")];
  buttons.forEach((b) => { if (b) b.disabled = true; });
  $("#pqStatus").textContent = refreshData ? "正在刷新数据源并检测…" : "正在检测…";
  $("#pqCount").textContent = "…";
  try {
    const data = await request("/api/monthly-exam/unreplied", { method: "POST", body: JSON.stringify({ since_days: sinceDays, refresh: refreshData }) });
    const rows = data.students || [];
    $("#pqStatus").textContent = "";
    $("#pqCount").textContent = String(rows.length);
    const tbody = $("#pqRows");
    if (!rows.length) {
      tbody.innerHTML = `<tr><td colspan="6" class="empty-cell">没有未回复家长</td></tr>`;
    } else {
      tbody.innerHTML = rows.map((r) => `<tr><td class="data-mono">${escapeHtml(r.parent_last_msg_at || "")}</td><td class="pq-msg">${escapeHtml(r.parent_last_msg || "")}</td><td class="data-mono">${escapeHtml(r.teacher_last_msg_at || "")}</td><td>${escapeHtml(r.teacher || "")}</td><td>${escapeHtml(r.parent_wechat || "")}</td><td class="data-mono">${escapeHtml(r.student_id || "")}</td></tr>`).join("");
    }
  } catch (error) { $("#pqStatus").textContent = error.message; }
  finally { buttons.forEach((b) => { if (b) b.disabled = false; }); }
}

$("#pqRun").addEventListener("click", () => runDetection(true));
$("#pqRefresh").addEventListener("click", () => runDetection(false));
