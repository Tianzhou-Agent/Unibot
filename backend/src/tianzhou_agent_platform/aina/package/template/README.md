# __NAME__

An AINA Protocol 2.0 app for Unibot, created with `aina init`.

```text
package/        what Unibot installs: aina.json, skills, canvas and widgets
server/         the MCP server with the tools; you run and host it yourself
```

## Develop

```bash
pip install -r server/requirements.txt
python server/main.py                 # serves __MCP_URL__
aina validate package --live          # checks aina.json, files and the server's tools
```

The canvas and widgets talk to Unibot only through `aina-ui.js` (vendored next to them); they cannot reach the
network themselves.

## Publish

Create a developer token in Unibot (Settings → Developer), then:

```bash
aina publish package --host https://<your-unibot>/api --token <token>
```

Publishing needs a platform admin's token. Users then install the app from Plugins, where they review the
permissions it asks for. Before publishing for real users, deploy the server on HTTPS and set `tools.mcp.url` in
`package/aina.json` to its address.
