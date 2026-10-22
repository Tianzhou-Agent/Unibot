# @unibot/aina-ui

The UI bridge for the canvas and widgets of a Superbot AINA Protocol 2.0 app. Your UI runs in a sandboxed iframe with
no network access; it reaches its tools and the conversation only through this bridge.

Use it from a bundler:

```bash
npm install @unibot/aina-ui
```

```js
import { host } from "@unibot/aina-ui";
import "@unibot/aina-ui/aina-ui.css"; // optional: Superbot's look, driven by the --aina-* theme variables

const { locale, config } = await host.getContext();
const { structuredContent } = await host.callTool("list_notes");
host.registerTool("highlight_notes", ({ note_ids }) => ({ structuredContent: { highlighted: note_ids.length } }));
```

Or without a build step: copy `aina-ui.js` (and `aina-ui.css`) next to your HTML and
`import { host } from "./aina-ui.js"`. `aina init` does this for you.

Importing the module starts the handshake with the Superbot shell. See `aina-ui.d.ts` for every method and the AINA
developer guide (`docs/aina-developer-guide.md` in the Superbot repository) for the rules the shell applies.

## Keeping canvas state

The canvas keeps nothing between openings: the iframe is reloaded, and while the user is away the agent's calls run in
a headless copy of your canvas on the server. Keep the state yourself, usually with your own app-visible tools that put
it in Host API storage (`load_board`/`save_board` in the whiteboard example, `load_deck`/`save_deck` in PPTist). Saving
this way needs no prompt: Superbot does not ask before `write` calls from your own UI, only before `destructive` ones.

Each rule below fixes a bug one of the examples had:

- **Load, then register.** Load the saved state before you call `registerTool`. Registered first, the agent's first
  call can draw on an empty canvas that the saved state then replaces.
- **In the background, save before you answer.** When `getContext().background` is true nobody is watching, and the
  tab the user opens later shows only what you saved. Save inside each tool handler that changes something, before it
  returns, instead of on a debounce. If the save fails, say so in the result, so the agent does not report work that
  was lost.
- **A failed save is not a reason to stop for good** in the background. Try again on the next change; in a visible tab
  you may stop and tell the user.
- **Per conversation, key the state by `conversation_id`.** The call context gives your MCP server the (pairwise)
  conversation the canvas is open beside. Check `getContext().conversation` for whether there is one.
- **On `context_changed`, flush first.** The user moved the canvas to another conversation, or a new one started.
  Save what is open now, but do not bind it to the conversation the canvas is now beside, then load that
  conversation's state. Run these steps one after another: a change that arrives while you load must not overwrite the
  wrong conversation's state.
- **State started before the conversation exists** belongs to it once the user's first message creates it: when
  `context_changed` goes from no conversation to one and the new conversation has nothing saved, save what is open to
  it.
- **Stay under the Host API limits:** 256 KiB per storage value and 10 MiB per file. Keep large state (a deck with
  pictures) as a file, and a small index or pointer as a value.

`examples/whiteboard-aina` shows the first three rules in a few lines; `examples/pptist-aina/canvas/bridge/aina-bridge.ts`
shows all of them.
