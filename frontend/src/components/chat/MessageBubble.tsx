import { Copy, Share2, Trash2, Paperclip, ArrowUp, X } from "lucide-react";
import { useState, type FormEvent } from "react";
import { classNames } from "@/lib/utils";
import type { ChatMessage, FileChip, MessageBlock } from "@/types";
import { SurfaceRenderer } from "./SurfaceRenderer";

export function UserMessage({ content, files }: { content: string; files?: FileChip[] }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[430px] rounded-lg bg-accent p-3.5 space-y-1.5">
        <p className="text-white text-[13px] leading-[1.42]">{content}</p>
        {files && files.length > 0 ? (
          <div className="flex flex-wrap gap-1.5">
            {files.map((f) => (
              <FileChipPill key={f.id} file={f} />
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function AssistantMessage({
  message,
  onChoice,
  onConfirm,
}: {
  message: ChatMessage;
  onChoice?: (choiceId: string) => void;
  onConfirm?: (action: "confirm" | "cancel") => void;
}) {
  return (
    <div className="rounded-lg border border-line bg-app-soft p-3 space-y-2">
      {message.content ? (
        <p className="text-ink text-[13px] leading-[1.5]">{message.content}</p>
      ) : null}
      {message.blocks?.map((b, i) => (
        <BlockRenderer key={i} block={b} />
      ))}
      {message.surface ? (
        <SurfaceRenderer
          block={message.surface}
          onAction={(id) => {
            if (message.surface?.kind === "choices") onChoice?.(id);
            if (message.surface?.kind === "confirm") onConfirm?.(id as "confirm" | "cancel");
          }}
        />
      ) : null}
      {message.role === "assistant" ? <AgentActions /> : null}
    </div>
  );
}

export function ThinkingBubble() {
  return (
    <div className="rounded-lg border border-accent-ring bg-accent-soft h-10 px-3 flex items-center gap-2.5">
      <span className="relative flex w-4 h-4 items-center justify-center">
        <span className="absolute inset-0 rounded-full border-2 border-accent-ring" />
        <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
      </span>
      <span className="text-accent-hover text-[13px] font-semibold">正在思考</span>
    </div>
  );
}

function BlockRenderer({ block }: { block: MessageBlock }) {
  if (block.kind === "text") {
    return <p className="text-ink text-[13px] leading-[1.5]">{block.text}</p>;
  }
  if (block.kind === "result") {
    return (
      <p className="text-ink-muted text-[12px] leading-[1.45] border-l-2 border-line pl-2.5">
        {block.text}
      </p>
    );
  }
  if (block.kind === "tool_call") {
    return (
      <div className="rounded-md border border-line bg-white p-2.5 text-[12px]">
        <div className="text-ink-muted">工具调用：{block.name}</div>
        {block.result ? (
          <div className="mt-1 text-ink">{block.result}</div>
        ) : null}
      </div>
    );
  }
  return null;
}

function FileChipPill({ file }: { file: FileChip }) {
  return (
    <span className="inline-flex items-center gap-1.5 h-7 px-2 rounded-lg border border-line bg-app-soft text-ink-muted text-[12px] font-semibold">
      <Paperclip className="w-3.5 h-3.5" />
      {file.name}
    </span>
  );
}

function AgentActions() {
  return (
    <div className="flex items-center justify-end gap-1.5">
      <ActionIcon icon={<Copy className="w-3.5 h-3.5" />} label="复制" />
      <ActionIcon icon={<Share2 className="w-3.5 h-3.5" />} label="分享" />
      <ActionIcon
        icon={<Trash2 className="w-3.5 h-3.5" />}
        label="删除"
        tone="danger"
      />
    </div>
  );
}

function ActionIcon({
  icon,
  label,
  tone = "default",
}: {
  icon: React.ReactNode;
  label: string;
  tone?: "default" | "danger";
}) {
  return (
    <button
      type="button"
      aria-label={label}
      className={classNames(
        "w-6 h-6 rounded-md hover:bg-line/50 flex items-center justify-center",
        tone === "danger" ? "text-danger" : "text-ink-muted",
      )}
    >
      {icon}
    </button>
  );
}

export function Composer({
  onSend,
  onAttach,
}: {
  onSend: (text: string) => void;
  onAttach?: () => void;
}) {
  const [text, setText] = useState("");
  const [attached, setAttached] = useState<FileChip | null>(null);

  function submit(e: FormEvent) {
    e.preventDefault();
    const trimmed = text.trim();
    if (!trimmed) return;
    onSend(trimmed);
    setText("");
  }

  return (
    <form
      onSubmit={submit}
      className="rounded-lg border border-line-strong bg-white p-2.5"
    >
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit(e);
          }
        }}
        placeholder="询问 World One、上传文件或打开画布…"
        rows={2}
        className="w-full bg-transparent text-[13px] leading-[1.45] text-ink placeholder:text-ink-muted outline-none resize-none"
      />
      <div className="mt-2 flex items-center gap-1.5">
        {attached ? (
          <span className="inline-flex items-center gap-1.5 h-7 px-2 rounded-lg border border-line bg-app-soft text-ink-muted text-[12px] font-semibold">
            <Paperclip className="w-3.5 h-3.5" />
            {attached.name}
            <button
              type="button"
              onClick={() => setAttached(null)}
              className="ml-0.5 hover:text-ink"
              aria-label="移除附件"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </span>
        ) : (
          <button
            type="button"
            onClick={() => {
              setAttached({
                id: "demo",
                name: "app-manifest.json",
                mimeType: "application/json",
                sizeBytes: 4382,
              });
              onAttach?.();
            }}
            className="inline-flex items-center gap-1.5 text-ink-muted text-[12px] font-semibold h-7 px-2 rounded-lg hover:bg-app-soft"
          >
            <Paperclip className="w-3.5 h-3.5" />
            添加附件
          </button>
        )}
        <span className="ml-auto" />
        <button
          type="submit"
          disabled={!text.trim()}
          className={classNames(
            "w-9 h-9 rounded-lg flex items-center justify-center text-white transition-colors",
            text.trim() ? "bg-accent hover:bg-accent-hover" : "bg-ink-subtle cursor-not-allowed",
          )}
          aria-label="发送"
        >
          <ArrowUp className="w-4 h-4" />
        </button>
      </div>
    </form>
  );
}
