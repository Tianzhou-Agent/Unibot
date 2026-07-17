import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { classNames } from "@/lib/utils";

const components: Components = {
  h1: ({ children }) => <h1 className="mt-4 first:mt-0 text-xl font-extrabold text-ink">{children}</h1>,
  h2: ({ children }) => <h2 className="mt-4 first:mt-0 text-lg font-extrabold text-ink">{children}</h2>,
  h3: ({ children }) => <h3 className="mt-3 first:mt-0 text-[15px] font-bold text-ink">{children}</h3>,
  p: ({ children }) => <p className="my-2 first:mt-0 last:mb-0 leading-[1.65]">{children}</p>,
  ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5">{children}</ol>,
  li: ({ children }) => <li className="pl-0.5">{children}</li>,
  blockquote: ({ children }) => (
    <blockquote className="my-3 border-l-2 border-accent pl-3 text-ink-muted">{children}</blockquote>
  ),
  a: ({ href, children }) => {
    const safeHref = href && /^(https?:|mailto:|\/|#)/i.test(href) ? href : undefined;
    return (
      <a
        href={safeHref}
        target={safeHref?.startsWith("http") ? "_blank" : undefined}
        rel={safeHref?.startsWith("http") ? "noreferrer" : undefined}
        className="font-semibold text-accent underline decoration-accent/35 underline-offset-2 hover:text-accent-hover"
      >
        {children}
      </a>
    );
  },
  code: ({ className, children, ...props }) => (
    <code
      className={classNames(
        "rounded bg-slate-900 px-1.5 py-0.5 font-mono text-[0.9em] text-slate-100",
        className,
      )}
      {...props}
    >
      {children}
    </code>
  ),
  pre: ({ children }) => (
    <pre className="my-3 overflow-x-auto rounded-lg bg-slate-950 p-3 text-[11.5px] leading-relaxed text-slate-100 [&>code]:bg-transparent [&>code]:p-0">
      {children}
    </pre>
  ),
  table: ({ children }) => (
    <div className="my-3 overflow-x-auto rounded-lg border border-line">
      <table className="w-full border-collapse text-left text-[12px]">{children}</table>
    </div>
  ),
  tbody: ({ children }) => <tbody className="[&>tr:last-child>td]:border-b-0">{children}</tbody>,
  th: ({ children }) => <th className="border-b border-line bg-app-soft px-3 py-2 font-bold">{children}</th>,
  td: ({ children }) => <td className="border-b border-line px-3 py-2">{children}</td>,
  hr: () => <hr className="my-4 border-line" />,
};

export function MarkdownContent({ content, className }: { content: string; className?: string }) {
  return (
    <div className={classNames("min-w-0 break-words text-[13px] text-ink", className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
