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
