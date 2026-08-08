const state = {
  schedules: [],
  tasks: [],
  weekdayLabels: ["周一", "周二", "周三", "周四", "周五", "周六", "周日"],
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const text = await response.text();
  let payload;
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`接口没有返回 JSON，请重启看板后再试：${path}`);
  }
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

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setClock() {
  $("#scheduleClock").textContent = new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date());
}

function parseWeeksText(value) {
  return String(value || "")
    .split(/[,\s，、]+/)
    .map((item) => Number(item.replace(/^w/i, "")))
    .filter((value) => Number.isInteger(value) && value > 0);
}

function weekdayText(weekdays) {
  return (weekdays || [])
    .map((day) => state.weekdayLabels[Number(day)] || `周${Number(day) + 1}`)
    .join("、");
}

function renderFormOptions() {
  const taskSelect = $("#scheduleForm").task_id;
  taskSelect.innerHTML = state.tasks.length
    ? state.tasks
        .map((task) => `<option value="${escapeHtml(task.id)}">${escapeHtml(task.group)} / ${escapeHtml(task.title)}</option>`)
        .join("")
    : '<option value="">没有可用操作</option>';

  const picker = $("#scheduleWeekdays");
  picker.innerHTML = state.weekdayLabels.map((label, index) => `
    <label class="weekday-option">
      <input type="checkbox" value="${index}">
      <span>${escapeHtml(label.replace("周", ""))}</span>
    </label>
  `).join("");
}

function updateCustomWeeksVisibility() {
  const field = $("#customWeeksField");
  if (!field) return;
  field.classList.toggle("is-hidden", $("#scheduleForm").week_mode.value !== "custom");
}

function resetForm() {
  const form = $("#scheduleForm");
  form.id.value = "";
  form.name.value = "";
  form.time.value = "09:00";
  form.week_mode.value = "current";
  form.weeks.value = "";
  form.enabled.checked = true;
  if (state.tasks.length) form.task_id.value = state.tasks[0].id;
  $$("#scheduleWeekdays input").forEach((input) => {
    input.checked = false;
  });
  updateCustomWeeksVisibility();
}

function fillForm(schedule) {
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
  updateCustomWeeksVisibility();
  $(".schedule-editor").scrollIntoView({ behavior: "smooth", block: "start" });
}

function readForm() {
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

function renderSchedules() {
  const list = $("#scheduleList");
  if (!state.schedules.length) {
    list.innerHTML = `
      <div class="schedule-empty">
        <strong>还没有定时任务</strong>
        <span>左侧设置好“周几 + 时间 + 操作”，保存后会显示在这里。</span>
      </div>
    `;
    return;
  }

  const tasksById = new Map(state.tasks.map((task) => [task.id, task]));
  list.innerHTML = state.schedules.map((schedule, index) => {
    const task = tasksById.get(schedule.task_id) || {};
    const weekText = schedule.week_mode === "custom"
      ? `指定 ${schedule.weeks.map((week) => `W${week}`).join("、")}`
      : "当前最新周";
    return `
      <article class="schedule-card pretty-card ${schedule.enabled ? "" : "disabled"}" draggable="true" data-schedule-id="${escapeHtml(schedule.id)}">
        <button class="drag-handle" type="button" aria-label="拖动排序" title="按住拖动调整顺序">
          <span></span><span></span><span></span>
        </button>
        <div class="schedule-card-main">
          <div class="schedule-card-top">
            <em class="schedule-order">#${String(index + 1).padStart(2, "0")}</em>
            <span class="schedule-status ${schedule.enabled ? "on" : "off"}">${schedule.enabled ? "已启用" : "已停用"}</span>
          </div>
          <strong>${escapeHtml(schedule.name)}</strong>
          <p>${escapeHtml(task.title || schedule.task_id)}</p>
          <div class="schedule-meta">
            <span>${escapeHtml(weekdayText(schedule.weekdays))}</span>
            <span>${escapeHtml(schedule.time)}</span>
            <span>${escapeHtml(weekText)}</span>
          </div>
          <small>${schedule.last_run_at ? `最近执行：${escapeHtml(schedule.last_run_at)} · ${escapeHtml(schedule.last_status || "")}` : "尚未执行"}</small>
        </div>
        <div class="schedule-card-actions">
          <button class="schedule-action" type="button" data-edit="${escapeHtml(schedule.id)}">编辑</button>
          <button class="schedule-action primary-action" type="button" data-run="${escapeHtml(schedule.id)}">立即执行</button>
          <button class="schedule-action" type="button" data-toggle="${escapeHtml(schedule.id)}">${schedule.enabled ? "停用" : "启用"}</button>
          <button class="schedule-action danger-action" type="button" data-delete="${escapeHtml(schedule.id)}">删除</button>
        </div>
      </article>
    `;
  }).join("");
  bindScheduleDrag();
}

function orderedScheduleIdsFromDom() {
  return $$("#scheduleList .schedule-card[data-schedule-id]")
    .map((card) => card.dataset.scheduleId)
    .filter(Boolean);
}

function reorderSchedulesInMemory(ids) {
  const scheduleById = new Map(state.schedules.map((schedule) => [schedule.id, schedule]));
  const ordered = ids.map((id) => scheduleById.get(id)).filter(Boolean);
  const orderedIds = new Set(ids);
  state.schedules
    .filter((schedule) => !orderedIds.has(schedule.id))
    .forEach((schedule) => ordered.push(schedule));
  state.schedules = ordered;
}

async function saveScheduleOrder() {
  const ids = orderedScheduleIdsFromDom();
  if (!ids.length) return;
  try {
    reorderSchedulesInMemory(ids);
    renderSchedules();
    const data = await postScheduleAction("/api/schedules/reorder", { ids });
    state.tasks = data.tasks || state.tasks;
    state.schedules = data.schedules || [];
    renderSchedules();
    showToast("任务顺序已保存");
  } catch (error) {
    showToast(`排序保存失败：${error.message}`);
    await loadSchedules();
  }
}

function bindScheduleDrag() {
  const list = $("#scheduleList");
  const cards = $$("#scheduleList .schedule-card[data-schedule-id]");
  let dragged = null;

  cards.forEach((card) => {
    card.addEventListener("dragstart", (event) => {
      if (event.target.closest("button:not(.drag-handle)")) {
        event.preventDefault();
        return;
      }
      dragged = card;
      card.classList.add("is-dragging");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", card.dataset.scheduleId || "");
    });

    card.addEventListener("dragend", () => {
      card.classList.remove("is-dragging");
      $$("#scheduleList .schedule-card.is-drop-target").forEach((item) => {
        item.classList.remove("is-drop-target");
      });
      if (dragged) saveScheduleOrder();
      dragged = null;
    });
  });

  if (!list.dataset.dragBound) {
    list.addEventListener("dragover", (event) => {
      const activeCard = $(".schedule-card.is-dragging");
      if (!activeCard) return;
      event.preventDefault();
      const target = event.target.closest(".schedule-card[data-schedule-id]");
      if (!target || target === activeCard) return;
      const rect = target.getBoundingClientRect();
      const shouldPlaceAfter = event.clientY > rect.top + rect.height / 2;
      list.insertBefore(activeCard, shouldPlaceAfter ? target.nextSibling : target);
      $$("#scheduleList .schedule-card.is-drop-target").forEach((item) => {
        item.classList.remove("is-drop-target");
      });
      target.classList.add("is-drop-target");
    });
    list.dataset.dragBound = "true";
  }
}

async function loadSchedules() {
  const data = await request("/api/schedules");
  state.tasks = data.tasks || [];
  state.schedules = data.schedules || [];
  state.weekdayLabels = data.weekday_labels || state.weekdayLabels;
  renderFormOptions();
  renderSchedules();
  if (!$("#scheduleForm").id.value) resetForm();
}

async function saveSchedule(event) {
  event.preventDefault();
  try {
    const data = await request("/api/schedules", {
      method: "POST",
      body: JSON.stringify(readForm()),
    });
    state.tasks = data.tasks || state.tasks;
    state.schedules = data.schedules || [];
    renderSchedules();
    resetForm();
    showToast("定时任务已保存");
  } catch (error) {
    showToast(error.message);
  }
}

async function postScheduleAction(path, payload) {
  const data = await request(path, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  state.tasks = data.tasks || state.tasks;
  state.schedules = data.schedules || [];
  renderSchedules();
  return data;
}

document.addEventListener("click", async (event) => {
  const edit = event.target.closest("[data-edit]");
  if (edit) {
    const schedule = state.schedules.find((item) => item.id === edit.dataset.edit);
    if (schedule) fillForm(schedule);
    return;
  }

  const toggle = event.target.closest("[data-toggle]");
  if (toggle) {
    const schedule = state.schedules.find((item) => item.id === toggle.dataset.toggle);
    if (!schedule) return;
    try {
      await postScheduleAction("/api/schedules/toggle", {
        id: schedule.id,
        enabled: !schedule.enabled,
      });
      showToast(schedule.enabled ? "定时任务已停用" : "定时任务已启用");
    } catch (error) {
      showToast(error.message);
    }
    return;
  }

  const remove = event.target.closest("[data-delete]");
  if (remove) {
    if (!confirm("确定删除这个定时任务吗？")) return;
    try {
      await postScheduleAction("/api/schedules/delete", { id: remove.dataset.delete });
      showToast("定时任务已删除");
    } catch (error) {
      showToast(error.message);
    }
    return;
  }

  const run = event.target.closest("[data-run]");
  if (run) {
    try {
      await postScheduleAction("/api/schedules/run-now", { id: run.dataset.run });
      showToast("已开始执行，请回主工作台查看运行记录");
    } catch (error) {
      showToast(error.message);
    }
  }
});

$("#scheduleForm").addEventListener("submit", saveSchedule);
$("#scheduleForm").week_mode.addEventListener("change", updateCustomWeeksVisibility);
$("#resetScheduleForm").addEventListener("click", resetForm);
$("#refreshSchedules").addEventListener("click", () => loadSchedules().then(() => showToast("已刷新")));

setClock();
setInterval(setClock, 1000);
loadSchedules().catch((error) => {
  $("#scheduleList").innerHTML = `<div class="schedule-empty"><strong>加载失败</strong><span>${escapeHtml(error.message)}</span></div>`;
});
