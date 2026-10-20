import { useTranslation } from "react-i18next";
import { useRef, useState, type MouseEvent } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, Download, Loader2, PanelRightOpen, RotateCcw, X } from "lucide-react";
import { api, apiErrorMessage, apiUrl } from "@/lib/api";
import { fileVisual, formatFileSize } from "@/lib/fileTypes";
import { useMockSession } from "@/lib/mockSession";
import { classNames } from "@/lib/utils";

type Preview = { state: "loading" } | { state: "ready"; text: string; truncated: boolean } | { state: "failed"; error: string };

/**
 * A file attached to a message: its type, name and size (or upload progress), and on hover or focus a card with the
 * start of its text, a download and, for a sent file, a link to it in the editor.
 */
export function AttachmentChip({ name, sizeBytes, fileRef, status = "ready", progress, error, to, onRemove, onRetry }: {
  name: string;
  sizeBytes: number;
  /** Set once the server holds the file: the card can then preview and download it. */
  fileRef?: string;
  status?: "uploading" | "ready" | "failed";
  /** How much of the upload was sent, 0 to 1. */
  progress?: number;
  error?: string;
  /** Where the chip opens the file: a sent file in the editor. */
  to?: string;
  onRemove?: () => void;
  onRetry?: () => void;
}) {
  const { t } = useTranslation("chatPage");
  const { profile } = useMockSession();
  const [open, setOpen] = useState(false);
  const [above, setAbove] = useState(true);
  const [preview, setPreview] = useState<Preview | null>(null);
  const anchorRef = useRef<HTMLDivElement | null>(null);
  const visual = fileVisual(name);
  const Icon = visual.icon;
  const actorQuery = new URLSearchParams({ user_id: profile.actorUserId, tenant_id: profile.tenantId }).toString();
  const stored = status === "ready" && fileRef;

  function show() {
    // The card opens on the side with room for it, so the message list does not cut it off.
    const top = anchorRef.current?.getBoundingClientRect().top ?? 0;
    setAbove(top > CARD_ROOM);
    setOpen(true);
    if (stored && !preview) void loadPreview(fileRef);
  }

  async function loadPreview(ref: string) {
    setPreview({ state: "loading" });
    try {
      const loaded = await api.get<{ text: string; truncated: boolean }>(`/attachments/${ref}/preview?${actorQuery}`);
      setPreview({ state: "ready", ...loaded });
    } catch (loadError) {
      setPreview({ state: "failed", error: apiErrorMessage(loadError) });
    }
  }

  function toggleOnTouch(event: MouseEvent) {
    // Without hover, a tap opens the card; its own link then opens the file.
    if (!window.matchMedia?.("(hover: none)").matches) return;
    event.preventDefault();
    if (open) setOpen(false);
    else show();
  }

  const percent = Math.round((progress ?? 0) * 100);
  const body = (
    <>
      <span className={classNames("flex h-8 w-8 shrink-0 items-center justify-center rounded-lg", status === "failed" ? "bg-danger-soft text-danger" : visual.tile)}>
        {status === "uploading" ? (
          <Loader2 className="h-4 w-4 animate-spin" role="img" aria-label={t("attachmentUploading", { name })} />
        ) : status === "failed" ? (
          <AlertTriangle className="h-4 w-4" aria-hidden />
        ) : (
          <Icon className="h-4 w-4" aria-hidden />
        )}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[12px] font-semibold text-ink">{name}</span>
        <span className={classNames("block truncate text-[10.5px]", status === "failed" ? "text-danger" : "text-ink-muted")}>
          {status === "uploading" ? t("attachmentProgress", { percent })
            : status === "failed" ? error ?? t("attachmentFailedShort")
              : `${visual.label} · ${formatFileSize(sizeBytes)}`}
        </span>
      </span>
    </>
  );
  const chipClass = classNames(
    "flex min-w-0 flex-1 items-center gap-2 rounded-xl py-1.5 pl-1.5 pr-1 text-left",
    to && "hover:bg-app-soft",
  );

  return (
    <div
      ref={anchorRef}
      data-status={status}
      className={classNames(
        "relative flex w-[220px] max-w-full items-center rounded-xl border bg-white",
        status === "failed" ? "border-danger-ring" : "border-line-strong",
      )}
      onMouseEnter={show}
      onMouseLeave={() => setOpen(false)}
      onFocus={show}
      onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}
    >
      {to ? <Link to={to} onClick={toggleOnTouch} className={chipClass}>{body}</Link> : <div className={chipClass} tabIndex={0} onClick={toggleOnTouch}>{body}</div>}
      {status === "failed" && onRetry ? (
        <button type="button" onClick={onRetry} className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-ink-muted hover:bg-app-soft hover:text-ink" aria-label={t("retryAttachment", { name })}>
          <RotateCcw className="h-3.5 w-3.5" />
        </button>
      ) : null}
      {onRemove ? (
        <button type="button" onClick={onRemove} className="mr-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-ink-muted hover:bg-app-soft hover:text-ink" aria-label={t("removeAttachment", { name })}>
          <X className="h-3.5 w-3.5" />
        </button>
      ) : null}
      {status === "uploading" ? (
        <span className="absolute inset-x-2 bottom-0 h-0.5 overflow-hidden rounded-full bg-line" aria-hidden>
          <span className="block h-full bg-accent transition-[width]" style={{ width: `${percent}%` }} />
        </span>
      ) : null}
      {open && status !== "failed" ? (
        <div
          role="dialog"
          aria-label={t("attachmentPreviewLabel", { name })}
          className={classNames(
            "absolute left-0 z-30 w-80 max-w-[calc(100vw-2rem)] rounded-xl border border-line-strong bg-white p-3 shadow-xl",
            above ? "bottom-full mb-2" : "top-full mt-2",
          )}
        >
          <div className="mb-2 flex items-center gap-2">
            <span className={classNames("flex h-6 w-6 shrink-0 items-center justify-center rounded-md", visual.tile)}><Icon className="h-3.5 w-3.5" aria-hidden /></span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[12px] font-semibold text-ink">{name}</span>
              <span className="block text-[10.5px] text-ink-muted">{visual.label} · {formatFileSize(sizeBytes)}</span>
            </span>
            {stored ? (
              <a href={apiUrl(`/attachments/${fileRef}?${actorQuery}`)} download={name} className="flex h-7 w-7 items-center justify-center rounded-md border border-line text-ink-muted hover:bg-app-soft hover:text-ink" aria-label={t("downloadAttachment", { name })} title={t("downloadAttachment", { name })}>
                <Download className="h-3.5 w-3.5" />
              </a>
            ) : null}
            {to ? (
              <Link to={to} className="flex h-7 w-7 items-center justify-center rounded-md border border-line text-ink-muted hover:bg-app-soft hover:text-ink" aria-label={t("openAttachment", { name })} title={t("openAttachment", { name })}>
                <PanelRightOpen className="h-3.5 w-3.5" />
              </Link>
            ) : null}
          </div>
          <div className="max-h-48 overflow-hidden whitespace-pre-wrap break-words rounded-lg bg-app-soft px-2.5 py-2 font-mono text-[10.5px] leading-5 text-ink-muted">
            {!stored ? t("attachmentPreviewPending")
              : !preview || preview.state === "loading" ? t("attachmentPreviewLoading")
                : preview.state === "failed" ? preview.error
                  : preview.text || t("attachmentPreviewEmpty")}
          </div>
          {preview?.state === "ready" && preview.truncated ? <p className="mt-1.5 text-[10px] text-ink-subtle">{t("attachmentPreviewMore")}</p> : null}
        </div>
      ) : null}
    </div>
  );
}

// Space the card needs above the chip before it opens above rather than below.
const CARD_ROOM = 300;
