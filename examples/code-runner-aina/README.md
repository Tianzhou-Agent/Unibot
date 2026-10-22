# Code Runner AINA example

Optional AINA 2.0 package. Superbot does not register, install or load it automatically.
Its MCP server imports no Superbot implementation code and executes Python, Bash and Node.js through a separate
sandboxd container. The canvas uses the ordinary AINA UI bridge; model and canvas calls ask for approval.

With Python 3.12+, from the repository root:

```sh
docker compose -f examples/code-runner-aina/docker-compose.yml up -d --build
python -m pip install -r examples/code-runner-aina/server/requirements.txt
python examples/code-runner-aina/server/main.py
```

The MCP server listens on `http://127.0.0.1:8768/mcp`; `CODE_RUNNER_SANDBOX_URL` defaults to
`http://127.0.0.1:18082`. Allow the MCP origin in Superbot's `UNIBOT_CAPABILITY_ALLOWED_ORIGINS`, then upload a ZIP
of the contents of `package/` (with `aina.json` at its root) in **Plugins → Add**, or publish it with:

```sh
aina publish examples/code-runner-aina/package --host http://127.0.0.1:8000
```

Install the app for your user before opening its canvas or asking the agent to run code. Uninstalling removes the
agent's access. Registration alone does not grant access. Change the manifest MCP URL when the host runs elsewhere.

Each call allows 1–110 seconds so it completes within the AINA protocol's 120-second tool-call limit.

This is a single-user local example: each deployment has one persistent sandbox workspace, independent of Superbot
conversation workspaces. Deploy a separate authenticated MCP/sandbox instance per trust boundary before sharing it.
The example exposes script execution and output; the old built-in runtime controls and platform history are no longer
part of the host UI. Superbot's sandbox infrastructure remains available to its managed runtimes.
