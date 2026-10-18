// aina dev page: the host side of the UI bridge (design §9.4), as the Unibot shell implements it, for one package.
// Packaged UI runs sandboxed with an opaque origin; the page answers only its own frames, relays app-visible tools,
// and asks before any call that changes data.
const PROTOCOL = "aina-ui/2.0";
const PACKAGE_SANDBOX = "allow-scripts allow-forms allow-downloads";
const URL_SANDBOX = "allow-scripts allow-same-origin allow-forms allow-popups allow-downloads";
const THEME = {
  "--aina-bg": "#FFFFFF", "--aina-surface": "#FAFAFA", "--aina-fg": "#0F1115", "--aina-muted": "#61666B",
  "--aina-border": "rgba(0, 0, 0, 0.10)", "--aina-accent": "#4176E6", "--aina-danger": "#DC2626",
  "--aina-radius": "12px", "--aina-font": 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif',
};
const MAX_WIDGET_HEIGHT = 480;
const $ = (id) => document.getElementById(id);

let state = null; // the host's view of the package (GET /dev/state)
let loadedKey = "";
let canvas = null; // { frame, bridge, tools: Set }
let logSeq = 0;
const logEntries = [];

// --- requests ------------------------------------------------------------------------------------------------------

async function api(method, path, body) {
  const response = await fetch(path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) throw new Error(data?.error?.message || data?.detail || `HTTP ${response.status}`);
  return data;
}

function log(kind, summary, details) {
  logEntries.push({ seq: `b${Date.now()}${Math.random()}`, at: Date.now() / 1000, kind, summary, ...details });
  renderLog();
}

// --- the bridge ----------------------------------------------------------------------------------------------------

function attachBridge(frame, { source, origin, context, extra }) {
  const approvedWrites = new Set();
  const waiting = new Map();
  let nextId = 1;
  const target = () => frame.contentWindow;

  async function handle(method, params) {
    const special = extra?.(method, params);
    if (special !== undefined) return special;
    const { record, installation } = state;
    switch (method) {
      case "initialize":
        return { protocol: PROTOCOL, locale: navigator.language || "en", theme: THEME, config: installation?.configuration ?? {}, ...context };
      case "callTool": {
        const name = String(params.name ?? "");
        const tool = record.manifest.runtime.tools.find((item) => item.name === name);
        if (!tool || !tool.visibility.includes("app") || tool.status !== "active") throw bridgeError("PERMISSION_DENIED", `Tool ${name} cannot be called from the app's UI`);
        // Design §6.1: destructive calls are confirmed every time, writes once per opened frame.
        const ask = tool.effect === "destructive" || (tool.effect === "write" && !approvedWrites.has(name));
        if (ask && !window.confirm(`${record.manifest.aina.name} wants to run ${name} (${tool.effect}):\n\n${JSON.stringify(params.arguments ?? {}, null, 2)}`)) {
          throw bridgeError("DENIED", "The user declined this call");
        }
        if (tool.effect === "write") approvedWrites.add(name);
        return api("POST", `/ainas/${encodeURIComponent(record.manifest.aina.id)}/app-tools/${encodeURIComponent(name)}`, {
          arguments: params.arguments ?? {}, source, confirmed: tool.effect !== "read",
        });
      }
      case "sendPrompt":
        log("bridge", `sendPrompt (the chat would receive): ${String(params.text ?? "").slice(0, 200)}`);
        return {};
      case "setTitle":
      case "notify":
        return {};
      default:
        throw bridgeError("METHOD_NOT_FOUND", `Unsupported bridge method ${method}`);
    }
  }

  function onMessage(event) {
    if (!target() || event.source !== target()) return;
    if (origin && event.origin !== origin) return;
    const message = event.data;
    if (!message || message.unibot !== PROTOCOL || message.id === undefined) return;
    if (typeof message.method !== "string") {
      const pending = waiting.get(String(message.id));
      if (!pending) return;
      waiting.delete(String(message.id));
      if (message.error) pending.reject(bridgeError(message.error.code ?? "TOOL_FAILED", message.error.message ?? "failed"));
      else pending.resolve(message.result ?? {});
      return;
    }
    const params = message.params && typeof message.params === "object" ? message.params : {};
    const reply = (body) => target().postMessage({ unibot: PROTOCOL, jsonrpc: "2.0", id: message.id, ...body }, origin ?? "*");
    Promise.resolve()
      .then(() => handle(message.method, params))
      .then(
        (result) => {
          log("bridge", `${source} → ${message.method}`, { request: params, response: result });
          reply({ result: result ?? {} });
        },
        (failure) => {
          log("bridge", `${source} → ${message.method} failed`, { request: params, error: failure.message });
          reply({ error: { code: failure.code ?? "TOOL_FAILED", message: failure.message } });
        },
      );
  }
  window.addEventListener("message", onMessage);

  return {
    detach: () => window.removeEventListener("message", onMessage),
    postEvent: (type, data) => target()?.postMessage({ unibot: PROTOCOL, jsonrpc: "2.0", method: "event", params: { type, data } }, origin ?? "*"),
    request: (method, params, timeoutMs = 30000) =>
      new Promise((resolve, reject) => {
        const id = `host-${nextId++}`;
        const timer = setTimeout(() => { waiting.delete(id); reject(bridgeError("TIMEOUT", `The frame did not answer ${method} in time`)); }, timeoutMs);
        waiting.set(id, { resolve: (value) => { clearTimeout(timer); resolve(value); }, reject: (error) => { clearTimeout(timer); reject(error); } });
        target()?.postMessage({ unibot: PROTOCOL, jsonrpc: "2.0", id, method, params }, origin ?? "*");
      }),
  };
}

function bridgeError(code, message) {
  return Object.assign(new Error(message), { code });
}

function uiUrl(entry) {
  const { id, version } = state.record.manifest.aina;
  return `/aina-ui/${encodeURIComponent(id)}/${encodeURIComponent(version)}/${entry.split("/").map(encodeURIComponent).join("/")}`;
}

// --- canvas --------------------------------------------------------------------------------------------------------

function renderCanvas() {
  canvas?.bridge.detach();
  canvas = null;
  const box = $("canvas-box");
  const pkg = state.record.manifest.runtime.package;
  if (!pkg.canvas) {
    box.innerHTML = '<p class="muted">This package has no canvas.</p>';
    renderCanvasTools();
    return;
  }
  $("canvas-title").textContent = `Canvas · ${pkg.canvas.title}`;
  const frame = document.createElement("iframe");
  const url = pkg.canvas.url ?? null;
  frame.title = pkg.canvas.title;
  frame.sandbox = url ? URL_SANDBOX : PACKAGE_SANDBOX;
  frame.referrerPolicy = "no-referrer";
  const tools = new Set();
  const bridge = attachBridge(frame, {
    source: "canvas",
    origin: url ? new URL(url).origin : null,
    context: { mode: "canvas", aina: { id: state.record.manifest.aina.id, version: state.record.manifest.aina.version } },
    extra: (method, params) => {
      if (method === "registerTool") {
        const name = String(params.name ?? "");
        if (!(pkg.canvas.tools ?? []).some((tool) => tool.name === name)) return Promise.reject(bridgeError("INVALID_REQUEST", `Canvas tool ${name} is not declared in aina.json`));
        tools.add(name);
        renderCanvasTools();
        return Promise.resolve({});
      }
      if (method === "setModelContext") {
        $("model-context").textContent = String(params.value ?? "").slice(0, 4000) || "(empty)";
        return Promise.resolve({});
      }
      if (method === "getIdentityToken") {
        if (!url) return Promise.reject(bridgeError("PERMISSION_DENIED", "Only URL canvases get an identity token"));
        return api("POST", `/ainas/${encodeURIComponent(state.record.manifest.aina.id)}/identity-token`);
      }
      return undefined;
    },
  });
  frame.src = url ?? uiUrl(pkg.canvas.entry);
  box.replaceChildren(frame);
  canvas = { frame, bridge, tools };
  renderCanvasTools();
}

function renderCanvasTools() {
  const declared = state?.record?.manifest.runtime.package.canvas?.tools ?? [];
  const select = $("canvas-tool");
  const current = select.value;
  select.replaceChildren(...declared.map((tool) => new Option(`${tool.name}${canvas?.tools.has(tool.name) ? "" : " (not registered yet)"}`, tool.name)));
  if (declared.some((tool) => tool.name === current)) select.value = current;
  $("canvas-call").disabled = !declared.length;
  if (!$("canvas-args").dataset.touched) $("canvas-args").value = skeleton(declared.find((tool) => tool.name === select.value)?.input_schema);
}

async function invokeCanvasTool() {
  const out = $("canvas-out");
  out.hidden = false;
  out.classList.remove("error");
  const name = $("canvas-tool").value;
  try {
    if (!canvas?.tools.has(name)) throw new Error(`The canvas has not registered ${name} (it must call host.registerTool)`);
    const args = JSON.parse($("canvas-args").value || "{}");
    const result = await canvas.bridge.request("invokeTool", { name, arguments: args }, 10000);
    log("bridge", `host → canvas invokeTool ${name}`, { request: args, response: result });
    out.textContent = JSON.stringify(result, null, 2);
  } catch (error) {
    out.classList.add("error");
    out.textContent = error.message;
  }
}

// --- agent tools ---------------------------------------------------------------------------------------------------

function agentTools() {
  return (state?.record?.manifest.runtime.tools ?? []).filter((tool) => tool.visibility.includes("model"));
}

function renderAgentTools() {
  const select = $("agent-tool");
  const current = select.value;
  const tools = agentTools();
  select.replaceChildren(...tools.map((tool) => new Option(`${tool.name} · ${tool.effect}${tool.status === "active" ? "" : ` · ${tool.status}`}`, tool.name)));
  if (tools.some((tool) => tool.name === current)) select.value = current;
  $("agent-call").disabled = !tools.length;
  if (!$("agent-args").dataset.touched) $("agent-args").value = skeleton(tools.find((tool) => tool.name === select.value)?.input_schema);
}

function widgetFrame({ widgetId, purpose, toolName, args, result, onPropose }) {
  const pkg = state.record.manifest.runtime.package;
  const declared = (pkg.widgets ?? []).find((item) => item.id === widgetId);
  if (!declared) return null;
  const wrapper = document.createElement("div");
  wrapper.className = "widget";
  wrapper.innerHTML = `<div class="label">${purpose} widget · ${widgetId}</div>`;
  const frame = document.createElement("iframe");
  frame.title = `${widgetId} widget`;
  frame.sandbox = PACKAGE_SANDBOX;
  frame.referrerPolicy = "no-referrer";
  frame.style.height = `${Math.min(declared.height ?? 160, MAX_WIDGET_HEIGHT)}px`;
  const bridge = attachBridge(frame, {
    source: "widget",
    origin: null,
    context: {
      mode: "widget",
      aina: { id: state.record.manifest.aina.id, version: state.record.manifest.aina.version },
      purpose,
      tool: toolName,
      arguments: args,
      result: purpose === "confirmation" ? null : result,
    },
    extra: (method, params) => {
      if (method === "resize") {
        const height = Math.min(Math.ceil(Number(params.height) || 0), MAX_WIDGET_HEIGHT);
        if (height <= 0) return Promise.reject(bridgeError("INVALID_REQUEST", "height must be a positive number"));
        frame.style.height = `${height}px`;
        return Promise.resolve({ height });
      }
      if (method === "proposeArguments") {
        if (purpose !== "confirmation" || !onPropose) return Promise.reject(bridgeError("PERMISSION_DENIED", "Only confirmation widgets may propose arguments"));
        onPropose(params.arguments ?? {});
        return Promise.resolve({});
      }
      if (method === "openCanvas") {
        $("canvas-pane").scrollIntoView({ behavior: "smooth" });
        return Promise.resolve({});
      }
      return undefined;
    },
  });
  frame.src = uiUrl(declared.entry);
  wrapper.append(frame);
  return { wrapper, bridge };
}

async function callAsAgent() {
  const out = $("agent-out");
  out.replaceChildren();
  const tool = agentTools().find((item) => item.name === $("agent-tool").value);
  if (!tool) return;
  let args;
  try {
    args = JSON.parse($("agent-args").value || "{}");
  } catch (error) {
    out.innerHTML = `<pre class="out error">${escapeHtml(error.message)}</pre>`;
    return;
  }
  const exposed = state.record.manifest.runtime.package.tools.expose[tool.name] ?? {};
  // A confirmation widget is shown inside the approval; Allow and Deny stay the host's own buttons.
  if (tool.effect !== "read") {
    const allowed = await new Promise((resolve) => {
      const box = document.createElement("div");
      box.className = "widget";
      box.innerHTML = `<div class="label">Approval: ${tool.name} (${tool.effect})</div>`;
      const proposed = document.createElement("pre");
      proposed.className = "out";
      proposed.textContent = JSON.stringify(args, null, 2);
      const confirmWidget = exposed.confirm_widget
        ? widgetFrame({ widgetId: exposed.confirm_widget, purpose: "confirmation", toolName: tool.name, args, onPropose: (edited) => { args = edited; proposed.textContent = `${JSON.stringify(edited, null, 2)}\n(edited by the widget)`; } })
        : null;
      if (confirmWidget) box.append(confirmWidget.wrapper);
      const actions = document.createElement("div");
      actions.className = "actions";
      const allow = Object.assign(document.createElement("button"), { type: "button", className: "primary", textContent: "Allow" });
      const deny = Object.assign(document.createElement("button"), { type: "button", textContent: "Deny" });
      allow.onclick = () => { confirmWidget?.bridge.detach(); resolve(true); };
      deny.onclick = () => { confirmWidget?.bridge.detach(); resolve(false); };
      actions.append(allow, deny);
      box.append(proposed, actions);
      out.append(box);
    });
    out.replaceChildren();
    if (!allowed) {
      out.innerHTML = '<pre class="out muted">Denied: the tool was not called.</pre>';
      return;
    }
  }
  try {
    const result = await api("POST", "/dev/agent-call", { tool: tool.name, arguments: args });
    const pre = document.createElement("pre");
    pre.className = "out";
    pre.textContent = `What the model sees:\n${JSON.stringify(result.visible, null, 2)}${result.task ? `\n\nBackground task ${result.task.id}: ${result.task.status}` : ""}`;
    out.append(pre);
    if (result.widget) {
      const widget = widgetFrame({
        widgetId: result.widget.widget_id ?? result.widget.id,
        purpose: result.widget.purpose ?? "result",
        toolName: tool.name,
        args,
        result: { content: result.widget.content, structuredContent: result.widget.structured_content },
      });
      if (widget) out.append(widget.wrapper);
    }
    canvas?.bridge.postEvent("tool_result", {});
  } catch (error) {
    out.innerHTML = `<pre class="out error">${escapeHtml(error.message)}</pre>`;
  }
}

// --- installation --------------------------------------------------------------------------------------------------

function renderInstallation() {
  const declared = state.record.manifest.runtime.package.permissions;
  const granted = new Set(state.installation?.granted_permissions ?? []);
  $("scopes").replaceChildren(
    ...declared.map((item) => {
      const label = document.createElement("label");
      const box = Object.assign(document.createElement("input"), { type: "checkbox", value: item.scope, checked: granted.has(item.scope), disabled: !item.optional });
      label.append(box, `${item.scope}${item.optional ? " (optional)" : ""}`);
      return label;
    }),
  );
  if (!$("config").dataset.touched) $("config").value = JSON.stringify(state.installation?.configuration ?? {}, null, 2);
}

async function reinstall() {
  try {
    const granted = [...$("scopes").querySelectorAll("input:checked")].map((box) => box.value);
    await api("POST", "/dev/install", { configuration: JSON.parse($("config").value || "{}"), granted_permissions: granted });
    $("config").dataset.touched = "";
    loadedKey = "";
    await refresh();
  } catch (error) {
    alert(error.message);
  }
}

// --- page ----------------------------------------------------------------------------------------------------------

async function refresh() {
  try {
    state = await api("GET", "/dev/state");
  } catch (error) {
    $("status").textContent = "host unreachable";
    $("status").className = "chip bad";
    return;
  }
  $("folder").textContent = state.folder;
  const problems = $("problems");
  problems.hidden = !state.errors.length && !state.warnings.length;
  problems.innerHTML = [
    ...state.errors.map((item) => `<div>✗ ${escapeHtml(item)}</div>`),
    ...state.warnings.map((item) => `<div class="warn">! ${escapeHtml(item)}</div>`),
  ].join("");
  $("status").textContent = state.errors.length ? "not registered" : "registered";
  $("status").className = `chip ${state.errors.length ? "bad" : "ok"}`;
  if (!state.record) return;
  const { aina, runtime } = state.record.manifest;
  $("app-name").textContent = aina.name;
  $("app-version").textContent = `${aina.id} ${aina.version}`;
  // Rebuild the frames when the package, its tools or the installation changed.
  const key = JSON.stringify([runtime.archive_sha256, runtime.tools_checked_at, state.installation?.installed_at, state.installation?.configuration]);
  if (key !== loadedKey) {
    loadedKey = key;
    renderCanvas();
    renderAgentTools();
    renderInstallation();
  }
}

async function pollLog() {
  try {
    const entries = await api("GET", `/dev/log?after=${logSeq}`);
    for (const entry of entries) logSeq = Math.max(logSeq, entry.seq);
    if (entries.length) {
      logEntries.push(...entries);
      renderLog();
    }
  } catch {
    // the host restarted or stopped; the status chip says so
  }
}

function renderLog() {
  logEntries.sort((a, b) => b.at - a.at);
  logEntries.splice(500);
  $("log").replaceChildren(
    ...logEntries.map((entry) => {
      const item = document.createElement("li");
      const { seq, at, kind, summary, ...details } = entry;
      const time = new Date(at * 1000).toLocaleTimeString();
      const extra = [details.status, details.ms !== undefined ? `${details.ms} ms` : null].filter((part) => part !== undefined && part !== null).join(" · ");
      item.innerHTML = `<details><summary><span class="kind ${kind}">${kind}</span><span>${escapeHtml(summary)}</span><span class="muted">${time}${extra ? ` · ${extra}` : ""}</span></summary><pre class="out">${escapeHtml(JSON.stringify(details, null, 2))}</pre></details>`;
      return item;
    }),
  );
}

function skeleton(schema) {
  const value = {};
  for (const [name, property] of Object.entries(schema?.properties ?? {})) {
    if (!(schema.required ?? []).includes(name)) continue;
    value[name] = property.default ?? ({ string: "", number: 0, integer: 0, boolean: false, array: [], object: {} })[property.type] ?? null;
  }
  return JSON.stringify(value, null, 2);
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
}

$("reload").onclick = async () => {
  await api("POST", "/dev/reload").catch(() => null);
  loadedKey = "";
  await refresh();
};
$("agent-call").onclick = () => void callAsAgent();
$("canvas-call").onclick = () => void invokeCanvasTool();
$("install").onclick = () => void reinstall();
$("agent-tool").onchange = () => { $("agent-args").dataset.touched = ""; renderAgentTools(); };
$("canvas-tool").onchange = () => { $("canvas-args").dataset.touched = ""; renderCanvasTools(); };
for (const id of ["agent-args", "canvas-args", "config"]) $(id).oninput = () => { $(id).dataset.touched = "1"; };

await refresh();
setInterval(refresh, 1500);
setInterval(pollLog, 1000);
