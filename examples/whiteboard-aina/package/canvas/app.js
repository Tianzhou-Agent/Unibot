// Whiteboard canvas (AINA Protocol 2.0 reference, design §9.5). The scene lives in the browser; the agent edits it
// through canvas tools, the user with the mouse, and Save stores it through the AINA's own MCP tool.
import { host } from "./aina-ui.js";

const SVG = "http://www.w3.org/2000/svg";
const board = document.getElementById("board");
const layer = document.getElementById("shapes");
const status = document.getElementById("status");
let scene = [];
let selected = null;
let tool = "select";
let nextId = 1;

const id = () => `el_${Date.now().toString(36)}_${nextId++}`;
const stroke = (color) => (/^#[0-9a-fA-F]{6}$/.test(color || "") ? color : "#1e1e1e");

function add(element) {
  scene.push({ id: id(), ...element, strokeColor: stroke(element.strokeColor) });
  changed();
  return scene[scene.length - 1];
}

function node(name, attributes) {
  const element = document.createElementNS(SVG, name);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
  return element;
}

function render() {
  layer.replaceChildren(
    ...scene.map((element) => {
      const group = node("g", { class: `shape${element.id === selected ? " selected" : ""}`, "data-id": element.id });
      const common = { stroke: element.strokeColor, "stroke-width": 2.2, fill: "none", "stroke-linecap": "round" };
      if (element.type === "rectangle") {
        group.append(node("rect", { ...common, x: element.x, y: element.y, width: element.width, height: element.height, rx: 8 }));
        if (element.text) {
          const label = node("text", { x: element.x + element.width / 2, y: element.y + element.height / 2 + 7, "text-anchor": "middle", fill: element.strokeColor });
          label.textContent = element.text;
          group.append(label);
        }
      } else if (element.type === "ellipse") {
        group.append(node("ellipse", { ...common, cx: element.x + element.width / 2, cy: element.y + element.height / 2, rx: element.width / 2, ry: element.height / 2 }));
      } else if (element.type === "arrow") {
        group.append(node("line", { ...common, x1: element.x, y1: element.y, x2: element.x + element.width, y2: element.y + element.height, "marker-end": "url(#head)" }));
      } else if (element.type === "text") {
        const text = node("text", { x: element.x, y: element.y, fill: element.strokeColor });
        text.textContent = element.text;
        group.append(text);
      }
      return group;
    }),
  );
}

// The agent sees a compact summary with every model request, so it rarely needs get_scene.
function summary() {
  if (!scene.length) return "The whiteboard is empty (1200x800 scene pixels).";
  const lines = scene.slice(0, 40).map((element) => {
    const box = `x=${Math.round(element.x)} y=${Math.round(element.y)} w=${Math.round(element.width || 0)} h=${Math.round(element.height || 0)}`;
    return `- ${element.id} ${element.type} ${box}${element.text ? ` text="${element.text}"` : ""}`;
  });
  return `${scene.length} shape(s) on the 1200x800 whiteboard:\n${lines.join("\n")}${scene.length > 40 ? "\n…" : ""}`;
}

let contextTimer;
function changed() {
  render();
  status.textContent = "Unsaved changes";
  clearTimeout(contextTimer);
  contextTimer = setTimeout(() => host.setModelContext(summary()), 300);
}

// --- The user's own editing ------------------------------------------------------------------------------------------

function point(event) {
  const matrix = board.getScreenCTM().inverse();
  const p = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix);
  return { x: p.x, y: p.y };
}

for (const button of document.querySelectorAll("[data-tool]")) {
  button.addEventListener("click", () => {
    tool = button.dataset.tool;
    for (const other of document.querySelectorAll("[data-tool]")) other.classList.toggle("active", other === button);
  });
}

let drag = null;
board.addEventListener("pointerdown", (event) => {
  const start = point(event);
  const target = event.target.closest(".shape");
  if (tool === "select") {
    selected = target ? target.dataset.id : null;
    const element = scene.find((item) => item.id === selected);
    drag = element ? { element, start, origin: { x: element.x, y: element.y } } : null;
    render();
  } else if (tool === "text") {
    // Sandboxed frames cannot open dialogs, so the text comes from the toolbar.
    const text = document.getElementById("text-value").value.trim();
    if (text) add({ type: "text", x: start.x, y: start.y, width: text.length * 10, height: 24, text });
  } else {
    const element = add({ type: tool, x: start.x, y: start.y, width: 1, height: 1 });
    drag = { element, start, creating: true };
  }
  board.setPointerCapture(event.pointerId);
});
board.addEventListener("pointermove", (event) => {
  if (!drag) return;
  const p = point(event);
  if (drag.creating) {
    drag.element.width = p.x - drag.start.x;
    drag.element.height = p.y - drag.start.y;
    if (drag.element.type !== "arrow") {
      drag.element.x = Math.min(drag.start.x, p.x);
      drag.element.y = Math.min(drag.start.y, p.y);
      drag.element.width = Math.abs(drag.element.width);
      drag.element.height = Math.abs(drag.element.height);
    }
  } else {
    drag.element.x = drag.origin.x + p.x - drag.start.x;
    drag.element.y = drag.origin.y + p.y - drag.start.y;
  }
  render();
});
board.addEventListener("pointerup", () => {
  if (drag) changed();
  drag = null;
});
document.addEventListener("keydown", (event) => {
  if (event.target instanceof HTMLInputElement) return;
  if ((event.key === "Delete" || event.key === "Backspace") && selected) {
    scene = scene.filter((item) => item.id !== selected);
    selected = null;
    changed();
  }
  const shortcuts = { v: "select", r: "rectangle", o: "ellipse", a: "arrow", t: "text" };
  const next = shortcuts[event.key.toLowerCase()];
  if (next) document.querySelector(`[data-tool="${next}"]`).click();
});

// --- Persistence: the scene is saved through the AINA's own MCP tool (design §9.5) ------------------------------------

document.getElementById("save").addEventListener("click", async () => {
  try {
    await host.callTool("save_board", { scene });
    status.textContent = "Saved";
  } catch (error) {
    status.textContent = error.message;
  }
});

// The saved board loads before the canvas tools are registered, so a drawing the agent makes is never replaced by it.
try {
  const { structuredContent } = await host.callTool("load_board", {});
  scene = structuredContent?.scene ?? [];
  render();
  status.textContent = scene.length ? "Loaded" : "";
} catch (error) {
  status.textContent = error.message;
}
host.setModelContext(summary());

// --- Canvas tools the agent can call -------------------------------------------------------------------------------

host.registerTool("get_scene", async () => ({ structuredContent: { width: 1200, height: 800, elements: scene } }));
host.registerTool("draw_circle", async ({ x, y, radius, color }) => {
  const element = add({ type: "ellipse", x: x - radius, y: y - radius, width: radius * 2, height: radius * 2, strokeColor: color });
  return { structuredContent: { element_id: element.id } };
});
host.registerTool("draw_rectangle", async ({ x, y, width, height, color, label }) => {
  const element = add({ type: "rectangle", x, y, width, height, strokeColor: color, text: label || "" });
  return { structuredContent: { element_id: element.id } };
});
host.registerTool("draw_arrow", async ({ from_x, from_y, to_x, to_y, color }) => {
  const element = add({ type: "arrow", x: from_x, y: from_y, width: to_x - from_x, height: to_y - from_y, strokeColor: color });
  return { structuredContent: { element_id: element.id } };
});
host.registerTool("add_text", async ({ x, y, text, color }) => {
  const element = add({ type: "text", x, y, width: text.length * 10, height: 24, text, strokeColor: color });
  return { structuredContent: { element_id: element.id } };
});
host.registerTool("clear_scene", async () => {
  const removed = scene.length;
  scene = [];
  selected = null;
  changed();
  return { structuredContent: { removed } };
});
