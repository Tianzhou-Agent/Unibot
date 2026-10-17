// @unibot/aina-ui — the UI bridge for AINA Protocol 2.0 canvases and widgets (design §9.4).
// Vendor this file into your package next to your HTML and import it as a module:
//   import { host } from "./aina-ui.js";
// It talks to the Unibot shell only through window.parent.postMessage; the shell applies every check and asks the
// user to confirm tool calls that change data.
const PROTOCOL = "aina-ui/2.0";
const pending = new Map();
const listeners = new Map();
const tools = new Map();
let nextId = 1;
let context;

window.addEventListener("message", (event) => {
  if (event.source !== window.parent) return;
  const message = event.data;
  if (!message || message.unibot !== PROTOCOL) return;
  if (message.method === "event") {
    const { type, data } = message.params || {};
    for (const listener of listeners.get(type) || []) listener(data);
    return;
  }
  if (message.method === "invokeTool") {
    invokeTool(message.id, message.params || {});
    return;
  }
  const call = pending.get(message.id);
  if (!call) return;
  pending.delete(message.id);
  if (message.error) call.reject(Object.assign(new Error(message.error.message), { code: message.error.code }));
  else call.resolve(message.result);
});

function request(method, params) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    // The frame does not know the shell's origin; only window.parent receives the message.
    window.parent.postMessage({ unibot: PROTOCOL, jsonrpc: "2.0", id, method, params }, "*");
  });
}

// The agent called a canvas tool (design §9.5): run its handler and answer the shell.
async function invokeTool(id, { name, arguments: args }) {
  const reply = (body) => window.parent.postMessage({ unibot: PROTOCOL, jsonrpc: "2.0", id, ...body }, "*");
  const handler = tools.get(name);
  if (!handler) {
    reply({ error: { code: "METHOD_NOT_FOUND", message: `Canvas tool ${name} is not registered` } });
    return;
  }
  try {
    const result = await handler(args || {});
    reply({ result: result ?? {} });
  } catch (error) {
    reply({ error: { code: "TOOL_FAILED", message: String((error && error.message) || error) } });
  }
}

function applyTheme(theme) {
  for (const [name, value] of Object.entries(theme || {})) {
    if (name.startsWith("--aina-")) document.documentElement.style.setProperty(name, String(value));
  }
}

export const host = {
  /** Locale, theme, non-secret configuration and, for widgets, the tool call it shows. */
  getContext() {
    context ??= request("initialize", {}).then((value) => {
      applyTheme(value.theme);
      return value;
    });
    return context;
  },
  /** Call one of this AINA's app-visible tools; the shell may ask the user first. Resolves to the MCP result. */
  callTool(name, args = {}) {
    return request("callTool", { name, arguments: args });
  },
  /** Send a message into the conversation as the user. */
  sendPrompt(text) {
    return request("sendPrompt", { text });
  },
  setTitle(text) {
    return request("setTitle", { text });
  },
  notify(text) {
    return request("notify", { text });
  },
  /** URL canvases only: a short-lived pairwise identity token for your own backend. */
  getIdentityToken() {
    return request("getIdentityToken", {}).then((value) => value.token);
  },
  /** Widgets only: open this AINA's canvas beside the conversation. */
  openCanvas() {
    return request("openCanvas", {});
  },
  /** Widgets only: request a height up to 480 px. */
  resize(height) {
    return request("resize", { height });
  },
  /** Confirmation widgets only: propose edited arguments for the pending call. */
  proposeArguments(args) {
    return request("proposeArguments", { arguments: args });
  },
  /**
   * Canvas only: implement a canvas tool declared in aina.json. The handler gets the validated arguments and returns
   * an MCP-shaped result, e.g. { structuredContent: {...} } or { content: [{ type: "text", text: "..." }] }.
   */
  registerTool(name, handler) {
    tools.set(name, handler);
    return request("registerTool", { name });
  },
  /** Canvas only: a compact summary of what the canvas shows (at most 4000 characters), sent with the agent's requests. */
  setModelContext(value) {
    return request("setModelContext", { value: String(value).slice(0, 4000) });
  },
  /** Listen to "tool_result" (the agent changed data in chat) or "context_changed". Returns an unsubscribe. */
  on(type, listener) {
    const set = listeners.get(type) || new Set();
    set.add(listener);
    listeners.set(type, set);
    return () => set.delete(listener);
  },
};

host.getContext();
