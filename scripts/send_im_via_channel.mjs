import "./ws-shim.mjs";

// 正确的 IM 发送：两步走
//   ① 用页面里已登录的 IM 通道真实投递（拿回 serverMsgId 作为 originId）
//   ② 调 CRM /im/msg/send 上报这条记录（带上 originId，这样 CRM 可见、且可以撤回）
// 只做①不做得不到 CRM 记录；只做②学生收不到、也没有 originId（撤不回）。
// 用法: node send_im_via_channel.mjs --port=9223 --page=user-board --user-id=<学生> --term-id=<期次> --im-id=<学生环信ID> --text=<内容> [--dry=1]
function arg(name, fallback) {
  const eq = name + "=";
  const hit = process.argv.find((a) => a.startsWith(eq));
  return hit ? hit.slice(eq.length) : fallback;
}

const PORT = Number(arg("--port", "9223"));
const PAGE_MATCH = arg("--page", "user-board");
const USER_ID = Number(arg("--user-id", "0"));
const TERM_ID = Number(arg("--term-id", "0"));
const IM_ID = arg("--im-id", "");
const TEXT = arg("--text", "");
const DRY = arg("--dry", "") === "1";

if (!USER_ID || !TERM_ID || !TEXT) {
  console.log(
    JSON.stringify({
      ok: false,
      error: "缺少参数：--user-id / --term-id / --text（--im-id 可省略，会自动查）",
    })
  );
  process.exit(1);
}

const targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
const page =
  targets.find((t) => t.type === "page" && t.url.includes(PAGE_MATCH)) ||
  targets.find((t) => t.type === "page" && /\.codemao\.cn\//.test(t.url));
if (!page) {
  console.log(JSON.stringify({ ok: false, error: "没有可用的 CRM 页面（请打开任意 codemao 页面并保持登录）" }));
  process.exit(1);
}

const ws = new WebSocket(page.webSocketDebuggerUrl);
const pending = new Map();
let seq = 0;
function send(method, params = {}, timeoutMs = 120000) {
  const id = ++seq;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    setTimeout(() => {
      if (pending.has(id)) {
        pending.delete(id);
        reject(new Error("timeout " + method));
      }
    }, timeoutMs);
  });
}
ws.addEventListener("message", (ev) => {
  const m = JSON.parse(ev.data);
  if (!m.id || !pending.has(m.id)) return;
  const { resolve, reject } = pending.get(m.id);
  pending.delete(m.id);
  m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
});
await new Promise((r) => ws.addEventListener("open", r, { once: true }));
await send("Runtime.enable");

// 原型反查需要在 Node 侧分步调用 CDP，所以这里用 queryObjects
const protoRes = await send("Runtime.evaluate", {
  expression: "window.WebIM && window.WebIM.connection && window.WebIM.connection.prototype",
});
if (!protoRes.result || !protoRes.result.objectId) {
  console.log(JSON.stringify({ ok: false, error: "拿不到 IM 连接原型，页面可能未登录" }));
  process.exit(1);
}
const found = await send("Runtime.queryObjects", { prototypeObjectId: protoRes.result.objectId });
const listId = found.objects && found.objects.objectId;
if (!listId) {
  console.log(JSON.stringify({ ok: false, error: "没有查到 IM 连接实例" }));
  process.exit(1);
}

const call = await send("Runtime.callFunctionOn", {
  objectId: listId,
  awaitPromise: true,
  returnByValue: true,
  functionDeclaration: `async function (userId, termId, imIdArg, content, dry) {
    var RT = 'https://cloud-gateway.codemao.cn/lbk-crm-realtime';
    var CRM = 'https://cloud-gateway.codemao.cn/crm-rocket';
    var out = { steps: [] };

    var conn = null;
    for (var i = 0; i < this.length; i++) {
      try { if (this[i].isOpened && this[i].isOpened()) { conn = this[i]; break; } } catch (e) {}
    }
    if (!conn) { out.ok = false; out.error = '没有已登录的 IM 连接'; return JSON.stringify(out); }

    // 没给 imId 就自己查（学生环信 ID 形如 1817971073_1736573415725）
    var imId = imIdArg;
    if (!imId) {
      try {
        var r0 = await fetch(CRM + '/im/teacher/studentList', { credentials: 'include' });
        var j0 = await r0.json();
        var stu = ((j0 && j0.students) || []).filter(function (s) { return s.userId === userId; })[0];
        imId = stu && ((stu.IMIdList || [])[0] || {}).IMId;
      } catch (e) {}
      if (!imId) { out.ok = false; out.error = '查不到该学员的 IM 号（userId=' + userId + '）'; return JSON.stringify(out); }
    }
    out.imId = imId;

    // ---- ① 真实投递 ----
    // 注意：消息内容必须是 CRM 自己的 JSON 协议（接收端会 JSON.parse，纯字符串会被判「消息格式错误」）
    //   type: 9 = Tl.txtMyself（老师发的文本） / 1 = Tl.txt（学生）
    //   参考 CRM 前端构造：{type, value, user_id, termId}
    var payload = JSON.stringify({ type: 9, value: content, user_id: userId, termId: termId });

    if (dry) {
      var probe;
      try { probe = WebIM.message.create({ chatType: 'singleChat', type: 'txt', to: imId, msg: payload }); }
      catch (e) { out.ok = false; out.error = '构造消息失败: ' + e.message; return JSON.stringify(out); }
      out.ok = true;
      out.dry = true;
      out.preview = JSON.stringify(probe).slice(0, 400);
      out.payload = payload;
      return JSON.stringify(out);
    }

    var msg;
    try { msg = WebIM.message.create({ chatType: 'singleChat', type: 'txt', to: imId, msg: payload }); }
    catch (e) { out.ok = false; out.error = '构造消息失败: ' + e.message; return JSON.stringify(out); }

    var sent = await new Promise(function (resolve) {
      var settled = false;
      var timer = setTimeout(function () {
        if (!settled) { settled = true; resolve({ ok: false, error: '通道发送超时（15s）' }); }
      }, 15000);
      try {
        conn.send(msg).then(function (res) {
          if (settled) return; settled = true; clearTimeout(timer);
          resolve({ ok: true, res: res || {} });
        }).catch(function (err) {
          if (settled) return; settled = true; clearTimeout(timer);
          resolve({ ok: false, error: String((err && (err.message || err.type || err.error)) || err) });
        });
      } catch (e) {
        if (settled) return; settled = true; clearTimeout(timer);
        resolve({ ok: false, error: 'send 抛异常: ' + e.message });
      }
    });
    if (!sent.ok) { out.ok = false; out.error = '通道发送失败: ' + sent.error; return JSON.stringify(out); }

    var info = sent.res || {};
    var originId = String(info.serverMsgId || (info.message && info.message.id) || '');
    var localId = String(info.localMsgId || '');
    out.steps.push({ step: 'channel', originId: originId, localMsgId: localId });
    if (!originId) { out.ok = false; out.error = '通道发送成功但没拿到 serverMsgId，无法上报/撤回'; out.raw = JSON.stringify(info).slice(0, 300); return JSON.stringify(out); }

    // ---- ② 上报到 CRM（带 originId） ----
    var now = Date.now();
    var hex = '';
    for (var k = 0; k < 32; k++) hex += '0123456789abcdef'[Math.floor(Math.random() * 16)];
    var msgId = hex + 'S' + userId + '_' + userId;
    var body = {
      termId: termId, sendTime: now, msgType: 1, msgId: msgId,
      userId: userId, platform: 1, content: content, originId: originId
    };
    try {
      var r = await fetch(RT + '/im/msg/send', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      var text = await r.text();
      out.steps.push({ step: 'report', status: r.status, body: text.slice(0, 200) });
      out.ok = r.status === 200;
      if (!out.ok) out.error = '上报返回 ' + r.status;
    } catch (e) {
      out.ok = false;
      out.error = '上报失败: ' + String((e && e.message) || e);
    }
    out.originId = originId;
    out.msgId = msgId;
    return JSON.stringify(out);
  }`,
  arguments: [
    { value: USER_ID },
    { value: TERM_ID },
    { value: IM_ID },
    { value: TEXT },
    { value: DRY },
  ],
});

if (call.exceptionDetails) {
  console.log(
    JSON.stringify({ ok: false, error: "页面异常", detail: JSON.stringify(call.exceptionDetails).slice(0, 400) })
  );
} else {
  console.log(call.result.value);
}
ws.close();
process.exit(0);
