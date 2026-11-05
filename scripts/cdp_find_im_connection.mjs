import "./ws-shim.mjs";

// 用 CDP 的 Runtime.queryObjects 捞出页面上真实的环信 connection 实例
// （页面用 Vue 生产构建，实例藏在闭包里，普通遍历找不到）
function arg(name, fallback) {
  const eq = name + "=";
  const hit = process.argv.find((a) => a.startsWith(eq));
  return hit ? hit.slice(eq.length) : fallback;
}

const PORT = Number(arg("--port", "9223"));
const PAGE_MATCH = arg("--page", "user-board");

const targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
// 优先匹配指定页面；找不到就退回任意 codemao 页面（同域共享登录态，页面切走也不会失效）
const page =
  targets.find((t) => t.type === "page" && t.url.includes(PAGE_MATCH)) ||
  targets.find((t) => t.type === "page" && /\.codemao\.cn\//.test(t.url));
if (!page) {
  console.log("未找到页面:", PAGE_MATCH);
  process.exit(1);
}

const ws = new WebSocket(page.webSocketDebuggerUrl);
const pending = new Map();
let seq = 0;
function send(method, params = {}, timeoutMs = 60000) {
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

// 1) 拿到 WebIM.connection 的 prototype 对象
const proto = await send("Runtime.evaluate", { expression: "window.WebIM && window.WebIM.connection && window.WebIM.connection.prototype" });
if (!proto.result || !proto.result.objectId) {
  console.log("拿不到 WebIM.connection.prototype:", JSON.stringify(proto.result));
  process.exit(1);
}
console.log("prototype objectId:", proto.result.objectId);

// 2) 找出所有以它为原型的实例
const found = await send("Runtime.queryObjects", { prototypeObjectId: proto.result.objectId });
const listId = found.objects && found.objects.objectId;
if (!listId) {
  console.log("queryObjects 没返回实例数组");
  process.exit(1);
}

// 3) 逐个看状态
const detail = await send("Runtime.callFunctionOn", {
  objectId: listId,
  functionDeclaration: `function () {
    return this.map(function (o, i) {
      var opened = 'n/a';
      try { opened = typeof o.isOpened === 'function' ? o.isOpened() : 'no-isOpened'; } catch (e) { opened = 'err:' + e.message; }
      var keys = [];
      try { keys = Object.keys(o).slice(0, 40); } catch (e) {}
      var userId = null;
      try { userId = o.userId || (o.context && o.context.userId) || null; } catch (e) {}
      var proto = [];
      try { proto = Object.getOwnPropertyNames(Object.getPrototypeOf(o)).filter(function (k) { return typeof o[k] === 'function'; }).slice(0, 60); } catch (e) {}
      return { index: i, opened: opened, userId: userId, methods: proto };
    });
  }`,
  returnByValue: true,
});

console.log("实例个数:", (detail.result.value || []).length);
console.log(JSON.stringify(detail.result.value, null, 1).slice(0, 3000));
ws.close();
process.exit(0);
