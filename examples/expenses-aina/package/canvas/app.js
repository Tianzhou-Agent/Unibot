// The canvas talks to Unibot only through the UI bridge (`@unibot/aina-ui`, AINA Protocol 2.0 §9.4).
// The bridge SDK ships with phase P3; until then this file documents the intended calls.
import { host } from "./aina-ui.js";

const body = document.querySelector("#expenses tbody");

async function render() {
  const { structuredContent } = await host.callTool("list_expenses", {});
  body.replaceChildren(
    ...structuredContent.items.map((item) => {
      const row = document.createElement("tr");
      row.dataset.id = item.id;
      row.innerHTML = `<td>${item.amount.toFixed(2)}</td><td>${item.category}</td><td></td>`;
      row.lastElementChild.textContent = item.note;
      return row;
    }),
  );
}

host.registerTool("highlight_rows", async ({ expense_ids }) => {
  for (const row of body.rows) row.classList.toggle("highlight", expense_ids.includes(row.dataset.id));
  return { structuredContent: { highlighted: expense_ids.length } };
});

host.on("tool_result", render);
render();
