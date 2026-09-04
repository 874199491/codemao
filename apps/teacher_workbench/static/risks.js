const $ = (selector) => document.querySelector(selector);

let riskData = null;
let levelSelect = null;
let classTimeSelect = null;
let activeLevel = "";

async function request(path) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" } });
  const contentType = response.headers.get("content-type") || "";
  const text = await response.text();
  if (!contentType.includes("application/json")) {
    throw new Error(text.trim().startsWith("<") ? "接口还没加载新代码，请重启教师工作台后再试。" : "接口没有返回 JSON。");
  }
  let payload;
  try {
    payload = JSON.parse(text);
  } catch {
    throw new Error("风险分层接口返回异常，请重启教师工作台后再试。");
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

function classTimeRank(value) {
  const text = String(value || "");
  if (text.includes("周五")) return 1;
  if (text.includes("周六午") || text.includes("周六中午")) return 2;
  if (text.includes("周六晚") || text.includes("周六晚上")) return 3;
  if (text.includes("周六")) return 4;
  return 9;
}

function levelTone(level) {
  return {
    high: "risk-high",
    follow: "risk-follow",
    stable: "risk-stable",
    excellent: "risk-excellent",
  }[level] || "risk-stable";
}

function renderSegments() {
  const segments = riskData?.segments || [];
  $("#riskSegments").innerHTML = segments.map((segment) => `
    <button class="risk-segment-card ${levelTone(segment.level)} ${activeLevel === segment.level ? "is-active" : ""}" type="button" data-level="${escapeHtml(segment.level)}">
      <span>${escapeHtml(segment.label)}</span>
      <strong>${Number(segment.count || 0)}</strong>
      <small>${Number(segment.percent || 0).toFixed(1).replace(".0", "")}% · 点击筛选</small>
    </button>
  `).join("") || '<div class="empty-state">还没有可用于分层的数据。</div>';
}

function setupClassTimeOptions() {
  const times = [...new Set((riskData?.students || []).map((row) => row.class_time).filter(Boolean))]
    .sort((a, b) => classTimeRank(a) - classTimeRank(b) || String(a).localeCompare(String(b), "zh-CN"));
  const select = $("#riskClassTime");
  const current = select.value;
  select.innerHTML = '<option value="">全部上课时间</option>' + times.map((time) => `<option value="${escapeHtml(time)}">${escapeHtml(time)}</option>`).join("");
  if (times.includes(current)) select.value = current;
  classTimeSelect?.destroy();
  classTimeSelect = new SlimSelect({ select: "#riskClassTime", settings: { showSearch: false, allowDeselect: true } });
}

function visibleStudents() {
  const keyword = $("#riskSearch").value.trim().toLowerCase();
  const level = $("#riskLevel").value;
  const classTime = $("#riskClassTime").value;
  return (riskData?.students || []).filter((row) => {
    const haystack = `${row.student_name || ""} ${row.student_id || ""}`.toLowerCase();
    return (!keyword || haystack.includes(keyword))
      && (!level || row.level === level)
      && (!classTime || row.class_time === classTime);
  });
}

function renderWeekStrip(row) {
  const weeks = row.week_statuses || [];
  if (!weeks.length) return '<div class="risk-week-empty">暂无多周缓存</div>';
  return weeks.map((item) => {
    const status = item.status || "未返回";
    const state = status === "已完课" ? "done" : status === "未到课" ? "danger" : "warn";
    return `<span class="risk-week ${state}"><b>${escapeHtml(item.label)}</b>${escapeHtml(status)}</span>`;
  }).join("");
}

function renderStudents() {
  const rows = visibleStudents();
  $("#riskStudents").innerHTML = rows.map((row) => `
    <article class="risk-student-card ${levelTone(row.level)}">
      <div class="risk-card-main">
        <div class="risk-card-top">
          <div>
            <h3>${escapeHtml(row.student_name || "未录姓名")}</h3>
            <button class="id-copy data-mono" type="button" data-student-id="${escapeHtml(row.student_id)}">${escapeHtml(row.student_id)}</button>
          </div>
          <span class="risk-score-pill">${escapeHtml(row.level_label)} · ${Number(row.risk_score || 0)}分</span>
        </div>
        <div class="risk-meta">
          <span>${escapeHtml(row.class_time || "未记录上课时间")}</span>
          ${row.class_name ? `<span>${escapeHtml(row.class_name)}</span>` : ""}
          <span>当前：${escapeHtml(row.current_status || "未返回")}</span>
        </div>
        <div class="risk-tags">${(row.tags || []).map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}</div>
        <div class="risk-reasons">${(row.reasons || []).map((reason) => `<span>${escapeHtml(reason)}</span>`).join("")}</div>
      </div>
      <div class="risk-card-side">
        <div class="risk-week-strip">${renderWeekStrip(row)}</div>
        <div class="risk-action"><span>建议动作</span><p>${escapeHtml(row.next_action || "保持观察。")}</p></div>
      </div>
    </article>
  `).join("") || `
    <div class="risk-empty">
      <strong>当前筛选没有学员</strong>
      <p>可以换一个层级或上课时间；如果全部为空，先执行一次完课数据更新。</p>
    </div>
  `;
}

function renderAll() {
  const currentWeek = riskData?.current_week ? `W${riskData.current_week}` : "--";
  $("#riskCurrentWeek").textContent = `${currentWeek} · ${riskData?.total || 0} 人`;
  $("#riskCheckedAt").textContent = `读取于 ${riskData?.checked_at || "--"}`;
  $("#riskMessage").textContent = riskData?.message || "风险分层基于本地缓存计算。";
  renderSegments();
  setupClassTimeOptions();
  renderStudents();
}

async function loadRisks() {
  $("#riskStudents").innerHTML = '<div class="empty-state">正在重新计算风险分层…</div>';
  try {
    riskData = await request("/api/student-risks");
    renderAll();
  } catch (error) {
    $("#riskSegments").innerHTML = '<div class="empty-state">加载失败</div>';
    $("#riskStudents").innerHTML = `<div class="risk-empty"><strong>加载失败</strong><p>${escapeHtml(error.message)}</p></div>`;
    showToast(error.message);
  }
}

async function copyText(value) {
  try {
    await navigator.clipboard.writeText(value);
  } catch {
    const node = document.createElement("textarea");
    node.value = value;
    node.style.position = "fixed";
    node.style.opacity = "0";
    document.body.appendChild(node);
    node.select();
    document.execCommand("copy");
    node.remove();
  }
}

$("#riskSearch").addEventListener("input", renderStudents);
$("#riskLevel").addEventListener("change", () => {
  activeLevel = $("#riskLevel").value;
  renderSegments();
  renderStudents();
});
$("#riskClassTime").addEventListener("change", renderStudents);
$("#refreshRisks").addEventListener("click", loadRisks);
$("#copyRiskIds").addEventListener("click", async () => {
  const ids = visibleStudents().map((row) => row.student_id).filter(Boolean);
  if (!ids.length) return showToast("当前筛选没有可复制的学员 ID");
  await copyText(ids.join("\n"));
  showToast(`已复制 ${ids.length} 个学员 ID`);
});
$("#riskSegments").addEventListener("click", (event) => {
  const card = event.target.closest("[data-level]");
  if (!card) return;
  activeLevel = activeLevel === card.dataset.level ? "" : card.dataset.level;
  $("#riskLevel").value = activeLevel;
  levelSelect?.setSelected(activeLevel || "");
  renderSegments();
  renderStudents();
});
$("#riskStudents").addEventListener("click", async (event) => {
  const button = event.target.closest(".id-copy");
  if (!button) return;
  await copyText(button.dataset.studentId || button.textContent.trim());
  showToast(`已复制 ${button.dataset.studentId || button.textContent.trim()}`);
});

levelSelect = new SlimSelect({ select: "#riskLevel", settings: { showSearch: false, allowDeselect: true } });
loadRisks();
