import { useEffect, useState } from "react";
import { Check, MessageSquareText, ThumbsDown, ThumbsUp, X } from "lucide-react";
import { classNames } from "@/lib/utils";
import { api, apiErrorMessage } from "@/lib/api";
import type { FeedbackRecord } from "@/types";

type FeedbackRating = "up" | "down";

const DOWN_REASONS = ["事实或结论错误", "未理解用户意图", "回答不完整", "响应速度慢", "工具结果错误", "其他"];

export function MessageFeedback({ messageId, conversationId }: { messageId: string; conversationId: string }) {
  const [feedback, setFeedback] = useState<FeedbackRecord | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [comment, setComment] = useState("");
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void api.get<FeedbackRecord | null>(`/feedback/messages/${encodeURIComponent(messageId)}`)
      .then((record) => { if (active) setFeedback(record); })
      .catch((reason) => { if (active) setError(apiErrorMessage(reason)); });
    setEditorOpen(false);
    setSaved(false);
    return () => { active = false; };
  }, [messageId]);

  async function persist(next: { rating: FeedbackRating; reason: string; comment: string } | null) {
    setSaving(true);
    setError("");
    try {
      if (next) {
        const record = await api.put<FeedbackRecord>(`/feedback/messages/${encodeURIComponent(messageId)}`, {
          conversation_id: conversationId,
          ...next,
        });
        setFeedback(record);
        setSaved(true);
        window.setTimeout(() => setSaved(false), 1600);
      } else {
        await api.delete<void>(`/feedback/messages/${encodeURIComponent(messageId)}`);
        setFeedback(null);
      }
    } catch (reason) {
      setError(apiErrorMessage(reason));
    } finally {
      setSaving(false);
    }
  }

  function chooseUp() {
    void persist({ rating: "up", reason: "", comment: "" });
    setEditorOpen(false);
  }

  function openDownEditor() {
    setReason(feedback?.rating === "down" ? feedback.reason : "");
    setComment(feedback?.rating === "down" ? feedback.comment : "");
    setEditorOpen(true);
  }

  function saveDown() {
    if (!reason) return;
    void persist({ rating: "down", reason, comment: comment.trim() });
    setEditorOpen(false);
  }

  return (
    <div className="relative flex items-center gap-1">
      {error ? <span className="mr-1 max-w-40 truncate text-[10px] text-danger" title={error}>提交失败</span> : null}
      {saved ? <span className="mr-1 inline-flex items-center gap-1 text-[10.5px] font-semibold text-success"><Check className="h-3 w-3" />已记录</span> : null}
      <button
        type="button"
        onClick={chooseUp}
        disabled={saving}
        aria-label={`点赞回答 ${messageId}`}
        aria-pressed={feedback?.rating === "up"}
        className={classNames("flex h-6 w-6 items-center justify-center rounded-md transition-colors", feedback?.rating === "up" ? "bg-success-soft text-success" : "text-ink-muted hover:bg-line/50 hover:text-success")}
      >
        <ThumbsUp className="h-3.5 w-3.5" />
      </button>
      <button
        type="button"
        onClick={openDownEditor}
        disabled={saving}
        aria-label={`点踩回答 ${messageId}`}
        aria-pressed={feedback?.rating === "down"}
        className={classNames("flex h-6 w-6 items-center justify-center rounded-md transition-colors", feedback?.rating === "down" ? "bg-danger-soft text-danger" : "text-ink-muted hover:bg-line/50 hover:text-danger")}
      >
        <ThumbsDown className="h-3.5 w-3.5" />
      </button>
      {feedback ? <button type="button" disabled={saving} onClick={() => void persist(null)} aria-label={`取消评价 ${messageId}`} className="ml-0.5 text-[10px] font-semibold text-ink-subtle hover:text-ink">取消评价</button> : null}

      {editorOpen ? (
        <div role="dialog" aria-label="提交点踩反馈" className="absolute right-0 top-8 z-40 w-[310px] rounded-xl border border-line bg-white p-4 text-left shadow-lg">
          <div className="flex items-start gap-2">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-danger-soft text-danger"><MessageSquareText className="h-4 w-4" /></span>
            <div><h3 className="text-[12.5px] font-bold text-ink">这次回答哪里需要改进？</h3><p className="mt-0.5 text-[10.5px] text-ink-muted">选择一个原因，可补充具体说明。</p></div>
            <button type="button" onClick={() => setEditorOpen(false)} aria-label="关闭反馈" className="ml-auto rounded p-1 text-ink-subtle hover:bg-app-soft hover:text-ink"><X className="h-3.5 w-3.5" /></button>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-1.5">
            {DOWN_REASONS.map((item) => <button key={item} type="button" onClick={() => setReason(item)} className={classNames("rounded-lg border px-2 py-1.5 text-left text-[10.5px] font-semibold", reason === item ? "border-accent bg-accent-soft text-accent" : "border-line text-ink-muted hover:bg-app-soft")}>{item}</button>)}
          </div>
          <textarea aria-label="反馈补充说明" value={comment} onChange={(event) => setComment(event.target.value.slice(0, 500))} rows={3} placeholder="补充说明（选填，最多 500 字）" className="mt-3 w-full resize-none rounded-lg border border-line px-2.5 py-2 text-[11px] text-ink outline-none placeholder:text-ink-subtle focus:border-accent" />
          <div className="mt-2 flex items-center gap-2"><span className="text-[9.5px] text-ink-subtle">{comment.length}/500</span><span className="flex-1" /><button type="button" onClick={() => setEditorOpen(false)} className="btn-ghost h-8 text-[11px]">取消</button><button type="button" onClick={saveDown} disabled={!reason} className={classNames("btn-primary h-8 text-[11px]", !reason && "cursor-not-allowed opacity-45")}>提交反馈</button></div>
        </div>
      ) : null}
    </div>
  );
}
