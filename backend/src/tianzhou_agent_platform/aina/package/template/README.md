# __NAME__

An AINA Protocol 2.0 app for Superbot, created with `aina init`.

```text
package/        what Superbot installs: aina.json, skills, canvas and widgets
server/         the MCP server with the tools; you run and host it yourself
```

## Develop

```bash
pip install -r server/requirements.txt
python server/main.py                 # serves __MCP_URL__
aina validate package --live          # checks aina.json, files and the server's tools
aina dev package                      # a local Superbot host at http://127.0.0.1:8790
```

`aina dev` runs the real Superbot host on your machine. Its page shows your canvas and widgets as users will see
them, lets you call tools as the agent and run canvas tools by hand, and lists every bridge message, MCP call and
Host API call. It reloads when you edit `package/`; press Reload after restarting your server.

The canvas and widgets talk to Superbot only through `aina-ui.js` (vendored next to them); they cannot reach the
network themselves.

## Publish

Create a developer token in Superbot (Settings → Developer), then:

```bash
aina publish package --host https://<your-unibot>/api --token <token>
```

Publishing needs a platform admin's token. Users then install the app from Plugins, where they review the
permissions it asks for. Before publishing for real users, deploy the server on HTTPS and set `tools.mcp.url` in
`package/aina.json` to its address.
