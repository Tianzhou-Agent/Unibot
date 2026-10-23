import { useTranslation } from "react-i18next";
import type { ReactNode } from "react";
import { Check, Download, Minus } from "lucide-react";
import { Topbar } from "@/components/layout/Topbar";
import { apiUrl } from "@/lib/api";

const PACKAGED = `<!-- canvas/index.html -->
<script type="module">
  import { host } from "./aina-ui.js";

  const { locale, config } = await host.getContext();
  const { structuredContent } = await host.callTool("list_notes");
  render(structuredContent, locale);

  // The agent changed data in chat: load it again.
  host.on("tool_result", async () => render((await host.callTool("list_notes")).structuredContent, locale));
</script>`;

const WEB_APP = `// At your app's startup. Only inside Superbot: opened on its own, your app runs as before.
if (window.parent !== window) {
  const { host } = await import("https://YOUR-UNIBOT/api/aina-ui/sdk/aina-ui.js");
  const { locale, theme } = await host.getContext();
  applyLanguage(locale);
}`;

const MANIFEST = `"canvas": {
  "url": "https://draw.example.com/unibot/",
  "title": "Whiteboard",
  "tools": [{
    "name": "highlight_shapes",
    "description": "Highlight shapes on the open whiteboard so the user can find them.",
    "inputSchema": {
      "type": "object",
      "properties": { "ids": { "type": "array", "items": { "type": "string" } } },
      "required": ["ids"]
    },
    "effect": "read"
  }]
}`;

const CANVAS_TOOLS = `host.registerTool("highlight_shapes", ({ ids }) => {
  board.select(ids);
  return { structuredContent: { highlighted: ids.length } };
});

// A short summary of what is on screen, sent with the agent's next requests.
board.onChange(() => host.setModelContext(\`\${board.shapes.length} shapes; selected: \${board.selection.join(", ")}\`));`;

/** How a canvas page connects to Superbot through @unibot/aina-ui, and what it gains. */
export default function CanvasBridgeHelpPage() {
  const { t } = useTranslation("canvasBridgeHelp");
  const without = ["show", "ownData", "mcp"] as const;
  const needs = ["context", "callTool", "refresh", "canvasTools", "modelContext", "prompt"] as const;
  return (
    <div className="flex h-full flex-col bg-app-bg">
      <Topbar
        title={t("title")}
        trail={{ label: t("breadcrumb"), links: [{ label: t("plugins"), to: "/plugin" }, { label: t("addApp"), to: "/plugin/new" }] }}
      />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <article className="mx-auto max-w-3xl px-5 py-7 text-[14px] leading-relaxed text-ink md:px-8 md:py-9">
          <h2 className="text-[26px] font-semibold">{t("title")}</h2>
          <p className="mt-2 text-ink-muted">{t("intro")}</p>

          <Section title={t("need.title")}>
            <div className="grid gap-3 sm:grid-cols-2">
              <List title={t("need.withoutTitle")} items={without.map((key) => t(`need.without.${key}`))} icon={<Check className="h-4 w-4 text-success" />} />
              <List title={t("need.needsTitle")} items={needs.map((key) => t(`need.needs.${key}`))} icon={<Minus className="h-4 w-4 text-ink-muted" />} />
            </div>
            <p className="mt-3 text-ink-muted">{t("need.excalidraw")}</p>
          </Section>

          <Section title={t("packaged.title")}>
            <p>{t("packaged.body")}</p>
            <div className="mt-3 flex flex-wrap gap-2">
              <a href={apiUrl("/aina-ui/sdk/aina-ui.js")} download="aina-ui.js" className="btn-outline rounded-lg"><Download className="h-4 w-4" />aina-ui.js</a>
              <a href={apiUrl("/aina-ui/sdk/aina-ui.css")} download="aina-ui.css" className="btn-outline rounded-lg"><Download className="h-4 w-4" />aina-ui.css</a>
            </div>
            <Code>{PACKAGED}</Code>
            <p className="mt-2 text-[12.5px] text-ink-muted">{t("packaged.note")}</p>
          </Section>

          <Section title={t("webApp.title")}>
            <p>{t("webApp.body")}</p>
            <Code>{WEB_APP}</Code>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-[12.5px] text-ink-muted">
              <li>{t("webApp.vendor")}</li>
              <li>{t("webApp.identity")}</li>
            </ul>
          </Section>

          <Section title={t("agent.title")}>
            <p>{t("agent.manifest")}</p>
            <Code>{MANIFEST}</Code>
            <p className="mt-3">{t("agent.code")}</p>
            <Code>{CANVAS_TOOLS}</Code>
          </Section>

          <Section title={t("check.title")}>
            <ol className="list-decimal space-y-1 pl-5">
              <li>{t("check.step1")}</li>
              <li>{t("check.step2")}</li>
              <li>{t("check.step3")}</li>
            </ol>
          </Section>
        </article>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-8">
      <h3 className="mb-2 text-[17px] font-semibold">{title}</h3>
      {children}
    </section>
  );
}

function List({ title, items, icon }: { title: string; items: string[]; icon: ReactNode }) {
  return (
    <div className="rounded-xl border border-line bg-white p-4">
      <h4 className="text-[13px] font-semibold">{title}</h4>
      <ul className="mt-2 space-y-1.5 text-[13px]">
        {items.map((item) => <li key={item} className="flex gap-2"><span className="mt-0.5 shrink-0">{icon}</span><span>{item}</span></li>)}
      </ul>
    </div>
  );
}

function Code({ children }: { children: string }) {
  return <pre className="mt-3 overflow-x-auto rounded-lg border border-line bg-app-soft p-3 font-mono text-[12px] leading-5 text-ink">{children}</pre>;
}
