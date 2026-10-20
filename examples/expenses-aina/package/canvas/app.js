// Expenses canvas: everything goes through the Superbot UI bridge; the frame has no network access of its own.
import { host } from "./aina-ui.js";

const rows = document.getElementById("rows");
const total = document.getElementById("total");
const status = document.getElementById("status");
const form = document.getElementById("add");

function cell(text, className) {
  const td = document.createElement("td");
  td.textContent = text;
  if (className) td.className = className;
  return td;
}

async function render() {
  try {
    const { structuredContent } = await host.callTool("list_expenses", {});
    const { currency } = (await host.getContext()).config;
    total.textContent = `${structuredContent.total.toFixed(2)} ${currency ?? "CNY"} in total`;
    rows.replaceChildren(
      ...structuredContent.items.map((item) => {
        const row = document.createElement("tr");
        row.dataset.id = item.id;
        const remove = document.createElement("button");
        remove.type = "button";
        remove.textContent = "Delete";
        remove.addEventListener("click", () => run(() => host.callTool("delete_expense", { expense_id: item.id })));
        const actions = document.createElement("td");
        actions.append(remove);
        row.append(cell(item.day), cell(item.category), cell(item.note), cell(item.amount.toFixed(2), "amount"), actions);
        return row;
      }),
    );
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
    // A call the user declined in Superbot's confirmation is reported as an error too.
    status.textContent = error.message;
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const data = new FormData(form);
  run(async () => {
    await host.callTool("add_expense", {
      amount: Number(data.get("amount")),
      category: String(data.get("category")),
      note: String(data.get("note") || ""),
    });
    form.reset();
  });
});

document.getElementById("summarize").addEventListener("click", () => {
  host.sendPrompt(`Summarize my expenses for ${new Date().toISOString().slice(0, 7)}.`);
});

// The agent changed data in chat: show it.
host.on("tool_result", render);
await render();

// Canvas tool (design §9.5): the agent highlights rows for the user to review, e.g. suspected duplicates.
host.registerTool("highlight_rows", async ({ expense_ids }) => {
  for (const row of rows.rows) row.classList.toggle("highlight", expense_ids.includes(row.dataset.id));
  return { structuredContent: { highlighted: expense_ids.filter((id) => rows.querySelector(`[data-id="${CSS.escape(id)}"]`)).length } };
});
