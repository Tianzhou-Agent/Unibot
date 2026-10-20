export default {
  title: "Connecting a canvas to Superbot",
  breadcrumb: "Breadcrumb",
  plugins: "Plugins",
  addApp: "Add app",
  intro:
    "A canvas is a web page Superbot shows beside the chat. On its own it only displays. To work with Superbot, the page loads a small script, @unibot/aina-ui. Loading it is what \"connected\" means in the Add app preview.",
  need: {
    title: "Do you need it?",
    withoutTitle: "Works without it",
    without: {
      show: "Showing the page beside the chat",
      ownData: "The page loading and saving its own data, from its own backend",
      mcp: "The agent changing that data through the app's MCP tools, when the page picks up changes by itself",
    },
    needsTitle: "Needs it",
    needs: {
      context: "Reading the user's language, Superbot's theme and the app's settings",
      callTool: "Calling the app's tools from the page, with Superbot asking the user when needed",
      refresh: "Refreshing when the agent changed data in chat",
      canvasTools: "Canvas tools: actions the agent performs on the page, such as highlighting shapes",
      modelContext: "Telling the agent what is on screen",
      prompt: "Sending a message into the chat from a button on the page",
    },
    excalidraw:
      "Example: the Excalidraw whiteboard works without it. Its MCP server draws through the whiteboard's own service, which updates the page live. With the script, the agent could also see your selection or highlight shapes for you.",
  },
  packaged: {
    title: "A canvas in your package",
    body: "Copy aina-ui.js (and, for Superbot's look, aina-ui.css) next to your canvas's index.html and import it. Starter projects from \"Download a starter project\" or aina init already include both.",
    note: "Importing the script connects at once. A packaged canvas has no network access, so all of its data goes through host.callTool.",
  },
  webApp: {
    title: "A web app canvas (canvas.url)",
    body: "Load the script once at startup. Your Superbot serves it, so you can import it from there while you develop:",
    vendor: "For production, copy aina-ui.js into your app's static files (or npm install @unibot/aina-ui), so your app doesn't depend on one Superbot's address.",
    identity: "To know who the user is on your own backend, call host.getIdentityToken() and verify the token with the Superbot JWKS.",
  },
  agent: {
    title: "Let the agent use the canvas",
    manifest: "Declare canvas tools in aina.json. The agent sees them like MCP tools, but the open canvas runs them:",
    code: "Implement them in the page, and keep the agent up to date with a short summary of the screen:",
  },
  check: {
    title: "Check it",
    step1: "Open Plugins → Add, and enter your canvas (a web address, or a bundle, then Check app).",
    step2: "Wait for the preview to load.",
    step3: "Under the preview, \"Connected to Superbot\" means the script loaded and the page talks to Superbot.",
  },
};
