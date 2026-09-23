const $ = (selector) => document.querySelector(selector);

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "请求失败");
  return data;
}

function showToast(message) {
  const toast = $("#toast");
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2600);
}

function renderJob(job) {
  const box = $("#ndJobStatus");
  if (!box || !job) return;
  box.hidden = false;
  const logs = (job.logs || []).slice(-16).map((line) => `<div>${String(line).replace(/[&<>]/g, (ch) => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[ch]))}</div>`).join("");
  box.innerHTML = `<b>${job.name || "国庆补课计划"}</b><div class="job-log-lines">${logs}</div>`;
  const text = (job.logs || []).join("\n");
  const match = text.match(/生成完成：(\d+) 张/);
  if (match) {
    $("#ndCount").textContent = match[1];
    $("#ndMeta").textContent = "已生成图片";
  }
}

async function pollJob(jobId) {
  let active = true;
  while (active) {
    const job = await request(`/api/jobs/${jobId}`);
    renderJob(job);
    active = ["queued", "running"].includes(job.status);
    if (active) await new Promise((resolve) => setTimeout(resolve, 1200));
  }
}

$("#ndGenerate")?.addEventListener("click", async () => {
  const button = $("#ndGenerate");
  button.disabled = true;
  $("#ndStatus").textContent = "正在创建批量生成任务…";
  try {
    const data = await request("/api/run", {
      method: "POST",
      body: JSON.stringify({ task_id: "national_day_makeup_plan", confirmed: true }),
    });
    $("#ndStatus").textContent = "正在生成图片，请稍等…";
    showToast("已开始生成国庆补课计划");
    await pollJob(data.job_id);
    $("#ndStatus").textContent = "生成完成，图片已保存到 data/国庆补课计划。";
  } catch (error) {
    $("#ndStatus").textContent = error.message;
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
});
