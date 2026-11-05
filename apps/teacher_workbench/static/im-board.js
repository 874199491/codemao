/* IM答疑：内嵌 CRM IM 看板 + 自动回复开关与运行日志。 */
(function () {
  "use strict";

  const el = {
    state: document.getElementById("imAutoState"),
    meta: document.getElementById("imAutoMeta"),
    toggle: document.getElementById("imAutoToggle"),
    status: document.getElementById("imAutoStatus"),
    refresh: document.getElementById("imBoardRefresh"),
    frame: document.getElementById("imBoardFrame"),
    log: document.getElementById("imAutoLog"),
  };

  const CRM_URL = "https://crm-common.codemao.cn/crm-common/user-board?from=crm1";
  let running = false;
  let busy = false;
  let timer = null;

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function fmtTime(ms) {
    if (!ms) return "";
    const d = new Date(Number(ms));
    if (Number.isNaN(d.getTime())) return "";
    const pad = (n) => String(n).padStart(2, "0");
    return pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + " " + pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
  }

  const STAGE_LABEL = {
    engine: "开关",
    detect: "发现消息",
    instant: "秒回",
    deep: "追答",
    skip: "只秒回",
    error: "异常",
  };

  function renderLog(entries) {
    if (!entries || !entries.length) {
      el.log.innerHTML = '<p class="im-ai-empty">还没有自动回复记录。</p>';
      return;
    }
    el.log.innerHTML = entries
      .map((entry) => {
        const stage = STAGE_LABEL[entry.stage] || entry.stage;
        const cls = entry.stage === "error" ? "error" : entry.stage === "deep" ? "deep" : entry.stage === "instant" ? "instant" : "";
        const who = entry.name ? '<b>' + esc(entry.name) + "</b> " : "";
        const extra = entry.summary ? '<span class="im-auto-note">' + esc(entry.summary) + "</span>" : "";
        return (
          '<div class="im-auto-row ' + cls + '">' +
          '<span class="im-auto-time">' + esc(entry.clock || fmtTime(entry.at)) + "</span>" +
          '<span class="im-auto-stage">' + esc(stage) + "</span>" +
          '<span class="im-auto-text">' + who + esc(entry.text) + extra + "</span>" +
          "</div>"
        );
      })
      .join("");
  }

  function render(data) {
    running = !!data.running;
    el.state.textContent = running ? "运行中" : "已停止";
    el.toggle.textContent = running ? "停止自动回复" : "启动自动回复";
    el.toggle.classList.toggle("secondary", running);

    const settings = data.settings || {};
    el.meta.textContent = running
      ? "每 " + settings.poll_seconds + " 秒检查一次" + (settings.deep_reply ? " · 含追答" : " · 仅秒回")
      : "尚未启动";

    const parts = [];
    if (running) parts.push("正在监听学员消息");
    if (data.tickCount) parts.push("已检查 " + data.tickCount + " 轮");
    if (data.handledCount) parts.push("已回复 " + data.handledCount + " 人");
    if (data.lastTickAt) parts.push("最近检查 " + fmtTime(data.lastTickAt));
    if (data.lastError) parts.push("错误：" + data.lastError);
    if (settings.instant_text) parts.push("秒回话术：" + settings.instant_text);
    el.status.textContent = parts.join(" · ") || "等待启动…";

    renderLog(data.log);
  }

  async function load() {
    try {
      const res = await fetch("/api/im-auto", { cache: "no-store" });
      render(await res.json());
    } catch (error) {
      el.status.textContent = "读取状态失败：" + error;
    }
  }

  el.toggle.addEventListener("click", async () => {
    if (busy) return;
    busy = true;
    el.toggle.disabled = true;
    try {
      const res = await fetch(running ? "/api/im-auto/stop" : "/api/im-auto/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const data = await res.json();
      if (data.error) alert(data.error);
    } catch (error) {
      alert("操作失败：" + error);
    }
    el.toggle.disabled = false;
    busy = false;
    await load();
  });

  el.refresh.addEventListener("click", () => {
    // 先置空再设回，强制 iframe 重新加载
    el.frame.src = "about:blank";
    setTimeout(() => {
      el.frame.src = CRM_URL;
    }, 120);
  });

  load();
  timer = setInterval(load, 5000);
  window.addEventListener("beforeunload", () => {
    if (timer) clearInterval(timer);
  });
})();
