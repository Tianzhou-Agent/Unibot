# Image Recognition AINA example

Optional AINA 2.0 package with an independent MCP server and a packaged React canvas. Superbot does not register,
install or load it automatically. The server imports no Superbot implementation code.

With Python 3.12+, from the repository root:

```sh
docker compose -f backend/docker-compose.vision.yml up -d --build
python -m pip install -r examples/image-recognition-aina/server/requirements.txt
python examples/image-recognition-aina/server/main.py
```

The vision service is exposed locally on port 18081 by this compose file.
Set `UNIBOT_VISION_BASE_URL` to its reachable URL. The server reads `UNIBOT_VISION_TIMEOUT_SECONDS` and
`UNIBOT_VISION_MAX_IMAGE_BYTES` from its own environment; it does not read Superbot's `.env`.
The MCP endpoint is `http://127.0.0.1:8767/mcp`. Keep this unauthenticated example on loopback.

Allow `http://127.0.0.1:8767` in Superbot's `UNIBOT_CAPABILITY_ALLOWED_ORIGINS`, then upload a ZIP of the contents of
`package/` (with `aina.json` at its root) in **Plugins → Add**, or publish with:

```sh
aina publish examples/image-recognition-aina/package --host http://127.0.0.1:8000
```

Install the app for your user, then open its canvas to select or paste an image. Registration alone does not grant
access. Detection tools are app-only and are never advertised to the chat model. Uninstalling removes access.
If Superbot runs in a container or on another machine, change the manifest MCP URL to a reachable endpoint.

The built canvas is included. To rebuild it after editing `canvas/`, use the shared frontend development toolchain:

```sh
cd frontend
npm ci
npm run build:canvases
```

This writes only to this example's `package/canvas/`; the regular frontend build does not include it.
