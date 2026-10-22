import { host } from "./aina-ui.js";

const form = document.querySelector("#runner");
const button = document.querySelector("#run");
const output = document.querySelector("#output");
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  button.disabled = true;
  output.textContent = "Running…";
  try {
    const result = await host.callTool(`run_${document.querySelector("#language").value}`, {
      script: document.querySelector("#script").value,
      timeout_seconds: Number(document.querySelector("#timeout").value),
      working_directory: document.querySelector("#directory").value,
    });
    if (result.isError) throw new Error(result.content?.map((item) => item.text ?? "").join("\n"));
    const execution = result.structuredContent;
    output.textContent = execution
      ? `${execution.status} · exit ${execution.exit_code ?? "—"}\n${execution.stdout}\n${execution.stderr}`
      : result.content?.map((item) => item.text ?? "").join("\n") ?? "Done";
  } catch (error) {
    output.textContent = error.message ?? String(error);
  } finally {
    button.disabled = false;
  }
});
