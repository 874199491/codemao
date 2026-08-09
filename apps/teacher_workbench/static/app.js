const state = {
  tasks: new Map(),
  metrics: new Map(),
  activeJobId: null,
  selectedJobId: null,
  activeMetric: null,
  pollTimer: null,
  pendingTask: null,
  loginPollTimer: null,
  availableWeeks: [],
  selectedWeeks: new Set(),
  config: null,
  profileCaptureTimer: null,
  schedules: [],
  scheduleTasks: [],
  weeklyKnowledge: {},
  weekdayLabels: ["周一", "周二", "周三", "周四", "周五", "周六", "周日"],
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `请求失败：${response.status}`);
  return payload;
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove("show"), 2800);
}

function normalizeWeeklyKnowledge(weeks = {}) {
  const result = {};
  Object.entries(weeks || {}).forEach(([week, value]) => {
    if (!value || typeof value !== "object") return;
    const weekKey = String(week).replace(/^W/i, "").trim();
    if (!weekKey) return;
    const topics = Array.isArray(value.topics)
      ? value.topics
      : String(value.topics || "")
        .split(/[\n,，、]+/);
    result[weekKey] = {
      topics: topics.map((item) => String(item).trim()).filter(Boolean),
      solid: String(value.solid || "").trim(),
      minor: String(value.minor || "").trim(),
      weak: String(value.weak || "").trim(),
    };
  });
  return result;
}

function syncWeeklyKnowledgeHidden() {
  const form = $("#configForm");
  if (!form?.feedback_weekly_knowledge) return;
  form.feedback_weekly_knowledge.value = JSON.stringify(state.weeklyKnowledge || {}, null, 2);
}

function renderWeeklyKnowledgeEditor(weeks = {}) {
  state.weeklyKnowledge = normalizeWeeklyKnowledge(weeks);
  syncWeeklyKnowledgeHidden();
  const editor = $("#weeklyKnowledgeEditor");
  if (!editor) return;
  const entries = Object.entries(state.weeklyKnowledge)
    .sort(([left], [right]) => Number(left) - Number(right));
  if (!entries.length) {
    editor.innerHTML = `
      <div class="weekly-knowledge-empty">
        还没有配置每周知识点。可以点击“从课程缓存生成”，系统会根据已抓取的课程标题生成草稿。
      </div>
    `;
    return;
  }
  editor.innerHTML = entries.map(([week, value]) => `
    <article class="weekly-knowledge-card" data-week="${escapeHtml(week)}">
      <div class="weekly-knowledge-card-head">
        <strong>W${escapeHtml(week)}</strong>
        <button class="ghost-link" type="button" data-remove-knowledge-week="${escapeHtml(week)}">删除</button>
      </div>
      <label>
        <span>本周重点</span>
        <input data-knowledge-field="topics" value="${escapeHtml((value.topics || []).join("、"))}" placeholder="例如：数组下标、数组遍历、边界条件">
      </label>
      <label>
        <span>S / 掌握很好时</span>
        <textarea data-knowledge-field="solid" rows="2" placeholder="孩子对……掌握得比较清楚">${escapeHtml(value.solid || "")}</textarea>
      </label>
      <label>
        <span>A+ / 有小细节时</span>
        <textarea data-knowledge-field="minor" rows="2" placeholder="整体能理解，但……还可以再巩固">${escapeHtml(value.minor || "")}</textarea>
      </label>
      <label>
        <span>A / 需要巩固时</span>
        <textarea data-knowledge-field="weak" rows="2" placeholder="建议重点巩固……">${escapeHtml(value.weak || "")}</textarea>
      </label>
    </article>
  `).join("");
}

function updateWeeklyKnowledgeFromEditor(event) {
  const card = event.target.closest?.(".weekly-knowledge-card");
  if (!card) return;
  const week = card.dataset.week;
  if (!week) return;
  const field = event.target.dataset.knowledgeField;
  if (!field) return;
  state.weeklyKnowledge[week] = state.weeklyKnowledge[week] || { topics: [], solid: "", minor: "", weak: "" };
  if (field === "topics") {
    state.weeklyKnowledge[week].topics = String(event.target.value || "")
      .split(/[\n,，、]+/)
      .map((item) => item.trim())
      .filter(Boolean);
  } else {
    state.weeklyKnowledge[week][field] = String(event.target.value || "").trim();
  }
  syncWeeklyKnowledgeHidden();
}

function addKnowledgeWeek() {
  const existing = Object.keys(state.weeklyKnowledge || {}).map(Number).filter(Number.isFinite);
  const nextWeek = String((existing.length ? Math.max(...existing) : 0) + 1);
  state.weeklyKnowledge[nextWeek] = {
    topics: [],
    solid: "",
    minor: "",
    weak: "",
  };
  renderWeeklyKnowledgeEditor(state.weeklyKnowledge);
}

async function suggestWeeklyKnowledge() {
  try {
    const data = await request("/api/feedback-knowledge-suggestions");
    const suggestions = normalizeWeeklyKnowledge(data.weeks || {});
    if (!Object.keys(suggestions).length) {
      showToast("还没有课程缓存。先更新一次课后学情反馈或完课数据后再生成。");
      return;
    }
    const merged = { ...suggestions, ...normalizeWeeklyKnowledge(state.weeklyKnowledge) };
    renderWeeklyKnowledgeEditor(merged);
    const polishText = {
      ai: "已根据课程缓存生成，并用 AI 润色。",
      ai_failed: "AI 润色失败，已使用本地模板生成。",
      ai_invalid: "AI 返回格式不正确，已使用本地模板生成。",
      local: "已根据课程缓存生成知识点草稿；如需 AI 润色，请配置 OPENAI_API_KEY 和 OPENAI_MODEL。",
    }[data.polish_status] || "已根据课程缓存生成知识点草稿。";
    showToast(`${polishText}可继续微调后保存。`);
  } catch (error) {
    showToast(error.message);
  }
}

function setClock() {
  const hour = new Date().getHours();
  const greeting = hour < 6 ? "夜深了" : hour < 12 ? "早上好" : hour < 18 ? "下午好" : "晚上好";
  $("#greeting").textContent = `${greeting}，今天从哪里开始？`;
  $("#clock").textContent = new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date());
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function applyConfig(config) {
  if (!config) return;
  state.config = config;
  document.title = config.dashboard_title || "教师工作台";
  const brandTitle = $(".brand strong");
  const brandSubtitle = $(".brand small");
  if (brandTitle) brandTitle.textContent = config.dashboard_title || "教师工作台";
  if (brandSubtitle) {
    brandSubtitle.textContent = [config.cohort_code || config.profile?.data_prefix, config.brand_subtitle]
      .filter(Boolean)
      .join(" · ");
  }
  const cohortCode = config.cohort_code || config.profile?.data_prefix || "";
  const heroCohortCode = $("#heroCohortCode");
  const allStudentsLabel = $("#allStudentsLabel");
  if (heroCohortCode) heroCohortCode.textContent = cohortCode || "--";
  if (allStudentsLabel) allStudentsLabel.textContent = cohortCode ? `${cohortCode} 学员` : "学员";
  const root = document.documentElement;
  const primary = config.theme?.primary || "#73AE52";
  const accent = config.theme?.accent || "#FBF1D7";
  root.style.setProperty("--green", primary);
  root.style.setProperty("--green-dark", primary);
  root.style.setProperty("--green-pale", accent);
  root.style.setProperty("--yellow", accent);
  root.style.setProperty("--paper", accent);
  populateConfigForm(config);
}

function populateConfigForm(config) {
  const form = $("#configForm");
  if (!form || !config) return;
  const feedback = config.feedback_rules || {};
  const regular = feedback.regular_exercise || {};
  const weekTest = feedback.week_test || {};
  const notes = feedback.notes || {};
  const homeworkCorrection = feedback.homework_correction || {};
  const rating = feedback.rating || {};
  const contact = feedback.contact || {};
  const templates = feedback.templates || {};
  form.cohort_code.value = config.cohort_code || "";
  form.cohort_start.value = config.cohort_start || "";
  form.crm_url.value = config.crm_url || "";
  form.feedback_regular_enabled.checked = regular.enabled !== false;
  form.feedback_regular_threshold.value = regular.mention_threshold ?? 80;
  form.feedback_week_full_only.checked = weekTest.mention_only_full_score !== false;
  form.feedback_week_full_text.value = weekTest.full_score_text || "周测100%正确";
  form.feedback_note_enabled.checked = notes.enabled !== false && notes.mention_if_submitted !== false;
  form.feedback_homework_correction_enabled.checked = homeworkCorrection.enabled !== false;
  form.feedback_contact_enabled.checked = contact.enabled !== false;
  form.feedback_rating_base.value = rating.base || "A";
  form.feedback_rating_excellent.value = rating.excellent || "A+";
  form.feedback_rating_top.value = rating.top || "S";
  form.feedback_rating_base_max.value = rating.base_max_combined_rate ?? 79;
  form.feedback_rating_threshold.value = rating.excellent_min_combined_rate ?? 80;
  form.feedback_rating_top_threshold.value = rating.top_min_combined_rate ?? 95;
  form.feedback_rating_template.value = rating.line_template || "本周综合评级：{grade}";
  form.feedback_contact_text.value = contact.text || "有什么问题您随时联系我哈～";
  form.feedback_openings.value = linesToText(templates.openings);
  form.feedback_completion_finished.value = linesToText(templates.completion_finished) || [
    "孩子这周两节课都已经学完了，整体学习节奏跟得上。",
    "本周两节课孩子都按时完成了，课程推进比较顺利。",
    "孩子已经完成本周两节课，整体学习进度是正常跟上的。",
    "这周两节课都有完成记录，说明孩子课后学习安排得还不错。",
    "本周课程孩子已经学完，后面主要就是把练习和知识点再梳理一遍。",
    "孩子这周的两节课都完成了，整体节奏保持得不错。",
  ].join("\n");
  form.feedback_performance_high.value = linesToText(templates.performance_high) || [
    "这周整体状态很好，课堂内容吸收得也不错。",
    "这周学习状态很在线，关键内容基本都跟上了。",
    "这周完成质量很高，说明孩子上课和练习都有认真跟进。",
    "这周的表现挺亮眼，说明相关知识点已经掌握得不错。",
    "这周不管是练习还是周测都完成得很好，继续保持这个节奏。",
  ].join("\n");
  renderWeeklyKnowledgeEditor(feedback.weekly_knowledge?.weeks || {});
  form.feedback_note_praise.value = linesToText(templates.note_praise);
  form.feedback_closings.value = linesToText(templates.closings);
  form.profile_json.value = JSON.stringify(config.profile || {}, null, 2);
}

function linesToText(value) {
  return Array.isArray(value) ? value.join("\n") : String(value || "");
}

function textToLines(value) {
  return String(value || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function readConfigForm() {
  const form = $("#configForm");
  let profile = {};
  let weeklyKnowledge = {};
  try {
    profile = JSON.parse(form.profile_json.value || "{}");
  } catch {
    throw new Error("高级配置 Profile 不是有效 JSON");
  }
  weeklyKnowledge = normalizeWeeklyKnowledge(state.weeklyKnowledge);
  const existing = state.config || {};
  return {
    dashboard_title: existing.dashboard_title || "教师工作台",
    cohort_code: form.cohort_code.value.trim() || profile.data_prefix || existing.cohort_code || "",
    brand_subtitle: existing.brand_subtitle || "",
    cohort_start: form.cohort_start.value || existing.cohort_start || "",
    week_length_days: Number(existing.week_length_days ?? 7),
    week_active_days: Number(existing.week_active_days ?? 5),
    manual_opened_week: Number(existing.manual_opened_week ?? 1),
    chrome_debug_port: Number(existing.chrome_debug_port ?? 9223),
    crm_url: form.crm_url.value.trim(),
    theme: {
      primary: existing.theme?.primary || "#73AE52",
      accent: existing.theme?.accent || "#FBF1D7",
    },
    invite: {
      friday_prefix: existing.invite?.friday_prefix || "周五",
      saturday_prefix: existing.invite?.saturday_prefix || "周六",
      workers: Number(existing.invite?.workers ?? 6),
    },
    feedback_rules: {
      regular_exercise: {
        enabled: form.feedback_regular_enabled.checked,
        label: "课中习题",
        mention_threshold: Number(form.feedback_regular_threshold.value),
        threshold_operator: ">",
      },
      week_test: {
        enabled: true,
        mention_only_full_score: form.feedback_week_full_only.checked,
        full_score_text: form.feedback_week_full_text.value.trim() || "周测100%正确",
        remind_if_missing: true,
      },
      notes: {
        enabled: form.feedback_note_enabled.checked,
        mention_if_submitted: form.feedback_note_enabled.checked,
      },
      homework_correction: {
        enabled: form.feedback_homework_correction_enabled.checked,
        text:
          existing.feedback_rules?.homework_correction?.text ||
          "课后作业里有错题的话，建议课后再抽一点时间完成订正，把出错的地方重新过一遍。",
      },
      rating: {
        enabled: true,
        base: form.feedback_rating_base.value.trim() || "A",
        excellent: form.feedback_rating_excellent.value.trim() || "A+",
        top: form.feedback_rating_top.value.trim() || "S",
        base_max_combined_rate: Number(form.feedback_rating_base_max.value),
        excellent_min_combined_rate: Number(form.feedback_rating_threshold.value),
        excellent_requires_week_test: true,
        top_min_combined_rate: Number(form.feedback_rating_top_threshold.value),
        top_requires_week_test_full_score: true,
        line_template: form.feedback_rating_template.value.trim() || "本周综合评级：{grade}",
      },
      contact: {
        enabled: form.feedback_contact_enabled.checked,
        text: form.feedback_contact_text.value.trim() || "有什么问题您随时联系我哈～",
        dedupe_keywords: ["有问题随时找我", "有问题随时联系我", "随时联系我"],
      },
      weekly_knowledge: {
        enabled: true,
        weeks: weeklyKnowledge,
      },
      templates: {
        openings: textToLines(form.feedback_openings.value),
        completion_finished: textToLines(form.feedback_completion_finished.value),
        performance_high: textToLines(form.feedback_performance_high.value),
        note_praise: textToLines(form.feedback_note_praise.value),
        closings: textToLines(form.feedback_closings.value),
      },
    },
    profile,
  };
}

async function saveConfig(event) {
  event.preventDefault();
  try {
    const data = await request("/api/config", {
      method: "POST",
      body: JSON.stringify(readConfigForm()),
    });
    applyConfig(data.config);
    await loadSummary();
    $("#configDialog").close();
    showToast(data.message || "配置已保存");
  } catch (error) {
    showToast(error.message);
  }
}

function profilePayload() {
  return {
    data_prefix:
      $("#profileDataPrefix").value.trim() ||
      $("#configForm")?.cohort_code?.value.trim() ||
      state.config?.cohort_code ||
      state.config?.profile?.data_prefix ||
      "new-teacher",
    dingtalk_url: $("#profileDingtalkUrl").value.trim(),
    node_id: $("#profileNodeId").value.trim(),
    learning_sheet_id: $("#profileLearningSheetId").value.trim(),
    class_pool_id: Number($("#profileClassPoolId")?.value || 0),
    auto_learning_sheet: $("#profileAutoLearningSheet").checked,
  };
}

function renderProfileCapture(status) {
  const badge = $("#profileCaptureBadge");
  if (!badge) return;
  badge.textContent = status.running ? "监听中" : "未监听";
  badge.className = `health-badge ${status.running ? "ok" : "pending"}`;
  const lines = [
    status.running ? "正在监听 CRM 网络。" : "当前未监听。",
    status.data_prefix ? `数据前缀：${status.data_prefix}` : "",
    status.capture_path ? `捕获文件：${status.capture_path}` : "",
    Number(status.capture_bytes || 0) ? `已捕获：${status.capture_bytes} bytes` : "",
    status.log_tail || "",
  ].filter(Boolean);
  $("#profileCaptureLog").textContent = lines.join("\n") || "等待开始监听。启动后，请在目标老师 CRM 里刷新班级看板。";
}

async function loadProfileCapture() {
  try {
    renderProfileCapture(await request("/api/profile-capture"));
  } catch (error) {
    console.error(error);
  }
}

async function startProfileCapture() {
  try {
    const status = await request("/api/profile-capture/start", {
      method: "POST",
      body: JSON.stringify(profilePayload()),
    });
    renderProfileCapture(status);
    showToast("已开始监听 CRM。现在请回到目标老师 CRM，重新刷新班级看板。");
    if (!state.profileCaptureTimer) {
      state.profileCaptureTimer = setInterval(loadProfileCapture, 1500);
    }
  } catch (error) {
    showToast(error.message);
  }
}

async function openProfileCrmLogin() {
  const button = $("#openProfileCrmLogin");
  button.disabled = true;
  try {
    const data = await request("/api/open-crm-login", {
      method: "POST",
      body: JSON.stringify({}),
    });
    showToast("已打开 CRM。请在新 Chrome 窗口登录目标老师账号。");
    $("#profileCaptureLog").textContent =
      `${data.message}\n\n下一步：在打开的 Chrome 里登录目标老师账号，然后进入/刷新班级看板，再回来看板点「开始监听 CRM」。`;
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
}

async function stopProfileCapture() {
  try {
    const status = await request("/api/profile-capture/stop", {
      method: "POST",
      body: JSON.stringify({}),
    });
    renderProfileCapture(status);
    showToast("已停止 CRM 监听。");
  } catch (error) {
    showToast(error.message);
  }
}

async function generateProfileFromCapture() {
  const button = $("#generateProfileFromCapture");
  button.disabled = true;
  try {
    const data = await request("/api/profile-capture/generate", {
      method: "POST",
      body: JSON.stringify(profilePayload()),
    });
    applyConfig(data.config);
    $("#configForm").profile_json.value = JSON.stringify(data.profile || data.config.profile || {}, null, 2);
    renderProfileCapture(data.capture);
    await loadSummary();
    showToast(data.message || "Profile 已生成。");
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
}

function chosenWeeks() {
  return [...state.selectedWeeks].sort((left, right) => left - right);
}

function updateWeekSelectionHint() {
  const selected = chosenWeeks();
  $("#weekSelectionHint").textContent = selected.length
    ? `已选择：${selected.map((week) => `W${week}`).join("、")}，任务将按此顺序执行`
    : "请至少选择一个周次";
  const allSelected =
    state.availableWeeks.length > 0
    && selected.length === state.availableWeeks.length;
  $("#selectAllWeeks").textContent = allSelected ? "仅选最新周" : "全选";
}

function renderWeekOptions(weeks, currentWeek) {
  const available = weeks.map((item) => Number(item.week));
  state.availableWeeks = available;
  state.selectedWeeks = new Set(
    chosenWeeks().filter((week) => available.includes(week))
  );
  if (!state.selectedWeeks.size && available.length) {
    state.selectedWeeks.add(Number(currentWeek));
  }
  $("#weekOptions").innerHTML = weeks.map((item) => {
    const week = Number(item.week);
    const checked = state.selectedWeeks.has(week) ? "checked" : "";
    return `
      <label class="week-option">
        <input type="checkbox" value="${week}" ${checked}>
        <span>W${week}</span>
        <small>第${item.courses[0]}-${item.courses[1]}课</small>
      </label>
    `;
  }).join("");
  updateWeekSelectionHint();
}

async function loadSummary() {
  try {
    const data = await request("/api/summary");
    applyConfig(data.config);
    const cohortCode = data.config?.cohort_code || data.config?.profile?.data_prefix || "";
    $("#checkedAt").textContent = data.checked_at.split(" ")[1];
    $("#chromeState").textContent = data.crm_logged_in
      ? "已登录"
      : data.chrome_9223_open
        ? "需要登录"
        : "未启动";
    $("#healthBadge").textContent = data.chrome_9223_ready ? "运行正常" : "需要登录";
    $("#healthBadge").className = `health-badge ${data.chrome_9223_ready ? "ok" : "bad"}`;
    const loginButton = $("#openCrmLogin");
    loginButton.hidden = data.crm_logged_in;
    loginButton.textContent = data.chrome_9223_open ? "继续完成 CRM 登录" : "打开 Chrome 登录";
    if (data.crm_logged_in && state.loginPollTimer) {
      clearInterval(state.loginPollTimer);
      state.loginPollTimer = null;
      showToast("CRM 登录成功，运行环境已就绪。");
    }
    const week = data.current_week;
    $("#weekBadge").textContent = [cohortCode, `W${week.week}`, `第${week.courses[0]}-${week.courses[1]}课`]
      .filter(Boolean)
      .join(" · ");
    $("#weekWindow").textContent = `W${week.week} · ${week.start.slice(5)}—${week.end.slice(5)}`;
    renderWeekOptions(data.available_weeks || [week], week.week);
    state.metrics.clear();
    data.metrics.forEach((metric) => {
      state.metrics.set(metric.id, metric);
      const card = $(`[data-metric="${metric.id}"]`);
      if (!card) return;
      card.querySelector("strong").textContent = metric.count;
      card.querySelector("em").textContent = `${metric.percent}%`;
      card.querySelector("i b").style.width = `${metric.percent}%`;
      card.title = `${metric.description}，点击查看 ${metric.count} 位学员`;
    });
    if (state.activeMetric && $("#detailDialog").open) {
      const updatedMetric = state.metrics.get(state.activeMetric.id);
      if (updatedMetric) openMetric(updatedMetric);
    }
  } catch (error) {
    showToast(error.message);
  }
}

function renderTasks(tasks) {
  const groups = new Map();
  const visibleTasks = (tasks || []).filter((task) => task.id !== "status" && task.group !== "日常检查");
  visibleTasks.forEach((task) => {
    state.tasks.set(task.id, task);
    if (task.surface && task.surface !== "main") return;
    if (!groups.has(task.group)) groups.set(task.group, []);
    groups.get(task.group).push(task);
  });
  let taskNumber = 1;
  $("#taskGroups").innerHTML = [...groups.entries()].map(([group, items]) => `
    <div class="task-group">
      <h3>${group}</h3>
      <div class="task-grid">
        ${items.map((task) => `
          <article class="task-card">
            <div class="task-card-head">
              ${iconMarkup(iconForTask(task))}
              <span class="task-number">${String(taskNumber++).padStart(2, "0")}</span>
              ${task.confirm ? '<span class="write-tag">写入操作</span>' : ""}
            </div>
            <h4>${task.title}</h4>
            <p>${task.description}</p>
            <button class="button ${task.confirm ? "primary" : "secondary"}" data-run="${task.id}">
              ${task.confirm ? "确认并运行" : "立即运行"}
            </button>
          </article>
        `).join("")}
      </div>
    </div>
  `).join("");
  animateTaskCards();
}

async function loadTasks() {
  try {
    const data = await request("/api/tasks");
    renderTasks(data.tasks);
  } catch (error) {
    $("#taskGroups").innerHTML = `<div class="empty-state">${error.message}</div>`;
  }
}

function weekdayText(weekdays) {
  return (weekdays || [])
    .map((day) => state.weekdayLabels[Number(day)] || `周${Number(day) + 1}`)
    .join("、");
}

function parseWeeksText(value) {
  return String(value || "")
    .split(/[,\s，、]+/)
    .map((item) => Number(item.replace(/^w/i, "")))
    .filter((value) => Number.isInteger(value) && value > 0);
}

function resetScheduleForm() {
  const form = $("#scheduleForm");
  if (!form) return;
  form.id.value = "";
  form.name.value = "";
  form.time.value = "09:00";
  form.week_mode.value = "current";
  form.weeks.value = "";
  form.enabled.checked = true;
  if (state.scheduleTasks.length) form.task_id.value = state.scheduleTasks[0].id;
  $$("#scheduleWeekdays input").forEach((input) => {
    input.checked = false;
  });
}

function fillScheduleForm(schedule) {
  const form = $("#scheduleForm");
  form.id.value = schedule.id || "";
  form.name.value = schedule.name || "";
  form.task_id.value = schedule.task_id || "";
  form.time.value = schedule.time || "09:00";
  form.week_mode.value = schedule.week_mode || "current";
  form.weeks.value = (schedule.weeks || []).join(",");
  form.enabled.checked = schedule.enabled !== false;
  const selected = new Set((schedule.weekdays || []).map(Number));
  $$("#scheduleWeekdays input").forEach((input) => {
    input.checked = selected.has(Number(input.value));
  });
  $("#schedules").scrollIntoView({ behavior: "smooth", block: "start" });
}

function readScheduleForm() {
  const form = $("#scheduleForm");
  return {
    id: form.id.value,
    name: form.name.value.trim(),
    task_id: form.task_id.value,
    time: form.time.value,
    week_mode: form.week_mode.value,
    weeks: parseWeeksText(form.weeks.value),
    enabled: form.enabled.checked,
    weekdays: $$("#scheduleWeekdays input")
      .filter((input) => input.checked)
      .map((input) => Number(input.value)),
  };
}

function renderScheduleFormOptions(tasks, weekdayLabels) {
  state.scheduleTasks = (tasks || []).filter(
    (task) => task.id !== "status" && task.group !== "日常检查" && (!task.surface || task.surface === "main")
  );
  state.weekdayLabels = weekdayLabels || state.weekdayLabels;
  const taskSelect = $("#scheduleForm select[name='task_id']");
  if (taskSelect) {
    taskSelect.innerHTML = state.scheduleTasks
      .map((task) => `<option value="${escapeHtml(task.id)}">${escapeHtml(task.group)} / ${escapeHtml(task.title)}</option>`)
      .join("");
  }
  const picker = $("#scheduleWeekdays");
  if (picker && !picker.children.length) {
    picker.innerHTML = state.weekdayLabels.map((label, index) => `
      <label class="weekday-option">
        <input type="checkbox" value="${index}">
        <span>${escapeHtml(label)}</span>
      </label>
    `).join("");
  }
}

function renderSchedules(schedules) {
  state.schedules = schedules || [];
  const list = $("#scheduleList");
  if (!list) return;
  if (!state.schedules.length) {
    list.innerHTML = '<div class="empty-state">还没有定时任务</div>';
    return;
  }
  const tasksById = new Map(state.scheduleTasks.map((task) => [task.id, task]));
  list.innerHTML = state.schedules.map((schedule) => {
    const task = tasksById.get(schedule.task_id) || {};
    const weekText = schedule.week_mode === "custom"
      ? `指定 ${schedule.weeks.map((week) => `W${week}`).join("、")}`
      : "当前最新周";
    return `
      <article class="schedule-card ${schedule.enabled ? "" : "disabled"}" data-schedule-id="${escapeHtml(schedule.id)}">
        <div>
          <strong>${escapeHtml(schedule.name)}</strong>
          <p>${escapeHtml(weekdayText(schedule.weekdays))} ${escapeHtml(schedule.time)} · ${escapeHtml(task.title || schedule.task_id)} · ${weekText}</p>
          <small>状态：${schedule.enabled ? "已启用" : "已停用"}${schedule.last_run_at ? ` · 最近：${escapeHtml(schedule.last_run_at)} · ${escapeHtml(schedule.last_status || "")}` : ""}</small>
        </div>
        <div class="schedule-card-actions">
          <button class="button secondary" type="button" data-schedule-edit="${escapeHtml(schedule.id)}">编辑</button>
          <button class="button secondary" type="button" data-schedule-run="${escapeHtml(schedule.id)}">立即执行</button>
          <button class="button secondary" type="button" data-schedule-toggle="${escapeHtml(schedule.id)}">${schedule.enabled ? "停用" : "启用"}</button>
          <button class="button secondary danger" type="button" data-schedule-delete="${escapeHtml(schedule.id)}">删除</button>
        </div>
      </article>
    `;
  }).join("");
}

async function loadSchedules() {
  try {
    const data = await request("/api/schedules");
    renderScheduleFormOptions(data.tasks || [], data.weekday_labels || []);
    renderSchedules(data.schedules || []);
    if (!$("#scheduleForm").id.value && state.scheduleTasks.length) {
      $("#scheduleForm").task_id.value = state.scheduleTasks[0].id;
    }
  } catch (error) {
    const list = $("#scheduleList");
    if (list) list.innerHTML = `<div class="empty-state">${error.message}</div>`;
  }
}

async function saveSchedule(event) {
  event.preventDefault();
  try {
    const data = await request("/api/schedules", {
      method: "POST",
      body: JSON.stringify(readScheduleForm()),
    });
    renderScheduleFormOptions(data.tasks || [], data.weekday_labels || []);
    renderSchedules(data.schedules || []);
    resetScheduleForm();
    showToast("定时任务已保存");
  } catch (error) {
    showToast(error.message);
  }
}

async function scheduleAction(path, payload) {
  const data = await request(path, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  renderScheduleFormOptions(data.tasks || [], data.weekday_labels || []);
  renderSchedules(data.schedules || []);
  if (data.job_id) {
    state.activeJobId = data.job_id;
    state.selectedJobId = data.job_id;
    pollJob(data.job_id);
  }
  return data;
}

async function taskOrReload(taskId) {
  let task = state.tasks.get(taskId);
  if (task) return task;
  await loadTasks();
  return state.tasks.get(taskId);
}

function askToRun(task) {
  if (!task) return;
  if (task.week_selectable && !chosenWeeks().length) {
    showToast("请至少选择一个更新周次。");
    return;
  }
  if (!task.confirm) {
    runTask(task, false);
    return;
  }
  state.pendingTask = task;
  $("#confirmTitle").textContent = task.title;
  const weekText = task.week_selectable
    ? `\n\n本次选择：${chosenWeeks().map((week) => `W${week}`).join("、")}。`
    : "";
  $("#confirmText").textContent = task.confirm_text + weekText;
  $("#confirmDialog").showModal();
}

async function runTask(task, confirmed) {
  if (state.activeJobId) {
    showToast("已有任务正在运行，请等待它完成。");
    return;
  }
  try {
    const data = await request("/api/run", {
      method: "POST",
      body: JSON.stringify({
        task_id: task.id,
        confirmed,
        weeks: task.week_selectable ? chosenWeeks() : undefined,
      }),
    });
    state.activeJobId = data.job_id;
    state.selectedJobId = data.job_id;
    $("#activity").scrollIntoView({ behavior: "smooth", block: "start" });
    setButtonsDisabled(true);
    pollJob(data.job_id);
    showToast(`已开始：${task.title}`);
  } catch (error) {
    showToast(error.message);
  }
}

function setButtonsDisabled(disabled) {
  $$("[data-run]").forEach((button) => {
    button.disabled = disabled;
  });
}

function statusLabel(status) {
  return {
    running: "运行中",
    success: "已完成",
    failed: "失败",
  }[status] || "等待";
}

function renderJob(job) {
  $("#jobStatus").textContent = statusLabel(job.status);
  $("#jobStatus").className = `job-status ${job.status}`;
  $("#terminalTitle").textContent = `${job.title} · ${job.id}`;
  const terminal = $("#terminal");
  terminal.textContent = job.logs.join("\n") || "任务已创建，等待输出…";
  terminal.scrollTop = terminal.scrollHeight;
}

function iconForTask(task) {
  const text = `${task.group || ""} ${task.title || ""} ${task.description || ""}`;
  if (/反馈|群发|消息|邀约|跟进/.test(text)) return "icon-message";
  if (/直播|到课|接龙/.test(text)) return "icon-live";
  if (/完课|学情|作业|补课/.test(text)) return "icon-book";
  if (/定时|时间/.test(text)) return "icon-clock";
  if (/配置|核对|检查|环境/.test(text)) return "icon-config";
  return task.confirm ? "icon-check" : "icon-bolt";
}

function iconMarkup(iconId) {
  return `<span class="task-icon" aria-hidden="true"><svg><use href="#${iconId}"></use></svg></span>`;
}

function classTimeRank(value) {
  const text = String(value || "");
  if (text.includes("周五")) return 1;
  if (text.includes("周六午")) return 2;
  if (text.includes("周六晚")) return 3;
  if (text.includes("周六")) return 4;
  return 99;
}

function filteredMetricStudents(metric) {
  if (!metric) return [];
  const needle = ($("#studentSearch")?.value || "").trim().toLowerCase();
  const classTime = $("#studentClassTime")?.value || "";
  return (metric.students || []).filter((student) => {
    const matchesSearch = !needle || `${student.name} ${student.id}`.toLowerCase().includes(needle);
    const matchesClassTime = !classTime || String(student.class_time || "未记录") === classTime;
    return matchesSearch && matchesClassTime;
  });
}

function renderClassTimeFilter(metric) {
  const select = $("#studentClassTime");
  if (!select) return;
  const current = select.value;
  const classTimes = [...new Set((metric.students || []).map((student) => String(student.class_time || "未记录")))]
    .sort((left, right) => classTimeRank(left) - classTimeRank(right) || left.localeCompare(right, "zh-CN"));
  select.innerHTML = [
    '<option value="">全部上课时间</option>',
    ...classTimes.map((classTime) => `<option value="${escapeHtml(classTime)}">${escapeHtml(classTime)}</option>`),
  ].join("");
  select.value = classTimes.includes(current) ? current : "";
}

function updateCopyButton(count) {
  const button = $("#copyIds");
  if (!button) return;
  button.textContent = count ? `复制当前 ${count} 个 ID` : "暂无可复制 ID";
  button.disabled = count === 0;
}

function renderStudentRows(metric) {
  const students = filteredMetricStudents(metric);
  $("#studentRows").innerHTML = students.length
    ? students.map((student) => `
      <tr>
        <td>${escapeHtml(student.name || "未记录")}</td>
        <td>${escapeHtml(student.id)}</td>
        <td><span class="student-status">${escapeHtml(student.status || "未分类")}</span></td>
        <td>${escapeHtml(student.class_time || "未记录")}</td>
        <td>${escapeHtml(student.class_name || "未记录")}</td>
      </tr>
    `).join("")
    : '<tr><td colspan="5" class="no-results">没有匹配的学员</td></tr>';
  updateCopyButton(students.length);
}

function openMetric(metric) {
  state.activeMetric = metric;
  const cohortCode = state.config?.cohort_code || state.config?.profile?.data_prefix || "全部";
  $("#detailTitle").textContent = metric.label;
  $("#detailSummary").textContent =
    `${metric.count} 人，占 ${cohortCode} 全部学员的 ${metric.percent}% · ${metric.description}`;
  $("#studentSearch").value = "";
  renderClassTimeFilter(metric);
  $("#studentClassTime").value = "";
  $("#sendFeedback").hidden = metric.id !== "finished";
  $("#cancelFeedbackSend").hidden = metric.id !== "finished";
  renderStudentRows(metric);
  if (!$("#detailDialog").open) $("#detailDialog").showModal();
}

async function copyMetricIds() {
  if (!state.activeMetric) return;
  const students = filteredMetricStudents(state.activeMetric);
  if (!students.length) {
    showToast("当前筛选下没有可复制的学生 ID");
    return;
  }
  const text = students.map((student) => student.id).join("\n");
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

async function pollJob(jobId) {
  clearTimeout(state.pollTimer);
  try {
    const job = await request(`/api/jobs/${jobId}`);
    renderJob(job);
    await loadJobs();
    if (job.status === "running") {
      state.pollTimer = setTimeout(() => pollJob(jobId), 900);
    } else {
      state.activeJobId = null;
      setButtonsDisabled(false);
      await loadSummary();
      showToast(job.status === "success" ? `${job.title}已完成` : `${job.title}运行失败`);
    }
  } catch (error) {
    state.activeJobId = null;
    setButtonsDisabled(false);
    showToast(error.message);
  }
}

async function loadJobs() {
  try {
    const data = await request("/api/jobs");
    if (!data.jobs.length) return;
    $("#jobList").innerHTML = data.jobs.map((job) => `
      <div class="job-item ${job.id === state.selectedJobId ? "active" : ""}" data-job-id="${job.id}">
        <strong>${job.title} · ${statusLabel(job.status)}</strong>
        <small>${job.started_at}${job.finished_at ? ` → ${job.finished_at.split(" ")[1]}` : ""}</small>
      </div>
    `).join("");
    const running = data.jobs.find((job) => job.status === "running");
    if (running && !state.activeJobId) {
      state.activeJobId = running.id;
      state.selectedJobId = running.id;
      setButtonsDisabled(true);
      pollJob(running.id);
    }
  } catch (error) {
    console.error(error);
  }
}

document.addEventListener("click", async (event) => {
  const runButton = event.target.closest("[data-run]");
  if (runButton) askToRun(state.tasks.get(runButton.dataset.run));

  const jobItem = event.target.closest("[data-job-id]");
  if (jobItem) {
    state.selectedJobId = jobItem.dataset.jobId;
    const job = await request(`/api/jobs/${state.selectedJobId}`);
    renderJob(job);
    loadJobs();
  }

  const nav = event.target.closest("[data-scroll]");
  if (nav) {
    $$("[data-scroll]").forEach((item) => item.classList.remove("active"));
    nav.classList.add("active");
    $(`#${nav.dataset.scroll}`).scrollIntoView({ behavior: "smooth" });
  }

  const metricCard = event.target.closest("[data-metric]");
  if (metricCard) {
    const metric = state.metrics.get(metricCard.dataset.metric);
    if (metric) openMetric(metric);
  }

  const editSchedule = event.target.closest("[data-schedule-edit]");
  if (editSchedule) {
    const schedule = state.schedules.find((item) => item.id === editSchedule.dataset.scheduleEdit);
    if (schedule) fillScheduleForm(schedule);
  }

  const toggleSchedule = event.target.closest("[data-schedule-toggle]");
  if (toggleSchedule) {
    const schedule = state.schedules.find((item) => item.id === toggleSchedule.dataset.scheduleToggle);
    if (!schedule) return;
    try {
      await scheduleAction("/api/schedules/toggle", {
        id: schedule.id,
        enabled: !schedule.enabled,
      });
      showToast(schedule.enabled ? "定时任务已停用" : "定时任务已启用");
    } catch (error) {
      showToast(error.message);
    }
  }

  const deleteSchedule = event.target.closest("[data-schedule-delete]");
  if (deleteSchedule) {
    if (!confirm("确定删除这个定时任务吗？")) return;
    try {
      await scheduleAction("/api/schedules/delete", { id: deleteSchedule.dataset.scheduleDelete });
      showToast("定时任务已删除");
    } catch (error) {
      showToast(error.message);
    }
  }

  const runSchedule = event.target.closest("[data-schedule-run]");
  if (runSchedule) {
    try {
      await scheduleAction("/api/schedules/run-now", { id: runSchedule.dataset.scheduleRun });
      showToast("已立即执行定时任务");
    } catch (error) {
      showToast(error.message);
    }
  }

  const removeKnowledgeWeek = event.target.closest("[data-remove-knowledge-week]");
  if (removeKnowledgeWeek) {
    delete state.weeklyKnowledge[removeKnowledgeWeek.dataset.removeKnowledgeWeek];
    renderWeeklyKnowledgeEditor(state.weeklyKnowledge);
  }
});

document.addEventListener("input", (event) => {
  if (event.target.closest?.("#weeklyKnowledgeEditor")) {
    updateWeeklyKnowledgeFromEditor(event);
  }
});

$("#weekOptions").addEventListener("change", (event) => {
  const input = event.target.closest('input[type="checkbox"]');
  if (!input) return;
  const week = Number(input.value);
  if (input.checked) state.selectedWeeks.add(week);
  else state.selectedWeeks.delete(week);
  updateWeekSelectionHint();
});

$("#selectAllWeeks").addEventListener("click", () => {
  const allSelected =
    state.availableWeeks.length > 0
    && chosenWeeks().length === state.availableWeeks.length;
  state.selectedWeeks = new Set(
    allSelected
      ? state.availableWeeks.slice(-1)
      : state.availableWeeks
  );
  $$("#weekOptions input").forEach((input) => {
    input.checked = state.selectedWeeks.has(Number(input.value));
  });
  updateWeekSelectionHint();
});

$("#openCrmLogin").addEventListener("click", async () => {
  const button = $("#openCrmLogin");
  button.disabled = true;
  try {
    const data = await request("/api/open-crm-login", {
      method: "POST",
      body: JSON.stringify({}),
    });
    showToast(data.message);
    await loadSummary();
    if (!state.loginPollTimer) {
      state.loginPollTimer = setInterval(loadSummary, 2000);
    }
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
});

$("#openConfig").addEventListener("click", () => {
  populateConfigForm(state.config);
  $("#profileDataPrefix").value = state.config?.profile?.data_prefix || state.config?.cohort_code || "";
  loadProfileCapture();
  $("#configDialog").showModal();
});
$("#runWorkbenchUpdate").addEventListener("click", async () => {
  try {
    const task = await taskOrReload("update_workbench");
    if (!task) {
      showToast("一键更新任务尚未加载，请刷新后再试。");
      return;
    }
    askToRun(task);
  } catch (error) {
    showToast(error.message);
  }
});
$("#closeConfig").addEventListener("click", () => $("#configDialog").close());
$("#cancelConfig").addEventListener("click", () => $("#configDialog").close());
$("#configForm").addEventListener("submit", saveConfig);
$("#addKnowledgeWeek").addEventListener("click", addKnowledgeWeek);
$("#suggestWeeklyKnowledge").addEventListener("click", suggestWeeklyKnowledge);
if ($("#scheduleForm")) $("#scheduleForm").addEventListener("submit", saveSchedule);
if ($("#resetScheduleForm")) $("#resetScheduleForm").addEventListener("click", resetScheduleForm);
$("#openProfileCrmLogin").addEventListener("click", openProfileCrmLogin);
$("#startProfileCapture").addEventListener("click", startProfileCapture);
$("#stopProfileCapture").addEventListener("click", stopProfileCapture);
$("#generateProfileFromCapture").addEventListener("click", generateProfileFromCapture);

$("#confirmDialog").addEventListener("close", () => {
  if ($("#confirmDialog").returnValue === "confirm" && state.pendingTask) {
    runTask(state.pendingTask, true);
  }
  state.pendingTask = null;
});

$("#closeDetails").addEventListener("click", () => $("#detailDialog").close());
$("#studentSearch").addEventListener("input", () => {
  if (state.activeMetric) renderStudentRows(state.activeMetric);
});
$("#studentClassTime").addEventListener("change", () => {
  if (state.activeMetric) renderStudentRows(state.activeMetric);
});
$("#copyIds").addEventListener("click", copyMetricIds);
$("#sendFeedback").addEventListener("click", async () => {
  const task = await taskOrReload("send_finished_feedback_w1");
  if (!task) {
    showToast("课后反馈任务尚未加载，请稍后再试。");
    return;
  }
  $("#detailDialog").close();
  askToRun(task);
});
$("#cancelFeedbackSend").addEventListener("click", async () => {
  const task = await taskOrReload("cancel_feedback_send");
  if (!task) {
    showToast("取消群发任务尚未加载，请重启看板后再试。");
    return;
  }
  $("#detailDialog").close();
  askToRun(task);
});

function buildHyperFramesTimeline() {
  if (!window.gsap) return;
  window.__timelines = window.__timelines || {};
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const timeline = gsap.timeline({
    paused: true,
    defaults: {
      duration: reduceMotion ? 0 : 0.56,
      ease: "power3.out",
      clearProps: "transform,opacity,visibility",
    },
  });
  timeline
    .from(".sidebar", { x: reduceMotion ? 0 : -28, autoAlpha: reduceMotion ? 1 : 0 }, 0)
    .from(".topbar", { y: reduceMotion ? 0 : -16, autoAlpha: reduceMotion ? 1 : 0 }, 0.08)
    .from(".overview-grid > *", {
      y: reduceMotion ? 0 : 24,
      autoAlpha: reduceMotion ? 1 : 0,
      stagger: reduceMotion ? 0 : 0.1,
    }, 0.18)
    .from(".metric-card", {
      y: reduceMotion ? 0 : 18,
      autoAlpha: reduceMotion ? 1 : 0,
      stagger: reduceMotion ? 0 : 0.07,
    }, 0.34)
    .from(".panel", {
      y: reduceMotion ? 0 : 20,
      autoAlpha: reduceMotion ? 1 : 0,
      stagger: reduceMotion ? 0 : 0.08,
    }, 0.5);
  window.__timelines["teacher-workbench"] = timeline;
  if (!window.__HYPERFRAMES_CAPTURE__) timeline.play(0);
}

function animateTaskCards() {
  if (!window.gsap || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  gsap.fromTo(
    ".task-card",
    { y: 12, autoAlpha: 0 },
    { y: 0, autoAlpha: 1, duration: 0.42, stagger: 0.06, ease: "power3.out", clearProps: "transform,opacity,visibility" },
  );
}

buildHyperFramesTimeline();
setClock();
setInterval(setClock, 1000);
Promise.all([loadTasks(), loadSummary(), loadJobs()]);
