// The canvas reaches its MCP server only through the Unibot UI bridge; the frame has no network access of its own.
import { host } from "./aina-ui.js";

const list = document.getElementById("notes");
const status = document.getElementById("status");
const form = document.getElementById("add");

async function render() {
  try {
    const { structuredContent } = await host.callTool("list_notes", {});
    list.replaceChildren(
      ...structuredContent.items.map((note) => {
        const item = document.createElement("li");
        item.dataset.id = note.id;
        const text = document.createElement("span");
        text.textContent = note.text;
        const remove = document.createElement("button");
        remove.type = "button";
        remove.textContent = "Delete";
        remove.addEventListener("click", () => run(() => host.callTool("delete_note", { note_id: note.id })));
        item.append(text, remove);
        return item;
      }),
    );
    // What the agent sees about the open canvas (design §9.5).
    host.setModelContext(`The canvas shows ${structuredContent.items.length} note(s).`);
  } catch (error) {
    status.textContent = error.message;
  }
}

async function run(action) {
  status.textContent = "";
  try {
    await action();
    await render();
  } catch (error) {
    // A call the user declined in Unibot's confirmation is reported as an error too.
    status.textContent = error.message;
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = String(new FormData(form).get("text") || "").trim();
  run(async () => {
    await host.callTool("add_note", { text });
    form.reset();
  });
});

// The agent changed notes in chat: show them.
host.on("tool_result", render);
await render();

// A canvas tool the agent can call while the canvas is open.
host.registerTool("highlight_notes", async ({ note_ids }) => {
  for (const item of list.children) item.classList.toggle("highlight", note_ids.includes(item.dataset.id));
  return { structuredContent: { highlighted: [...list.children].filter((item) => note_ids.includes(item.dataset.id)).length } };
});
