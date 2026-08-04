import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, Clock3, MessageSquareText, Search, ThumbsDown, ThumbsUp, UserRoundCheck } from "lucide-react";
import { Link } from "react-router-dom";
import { BarMeter, LineChart, MetricCard, MockDataBadge, SectionCard } from "@/components/analytics/DashboardPrimitives";
import { Topbar } from "@/components/layout/Topbar";
import { classNames } from "@/lib/utils";
import { FEEDBACK_REASON_DISTRIBUTION, FEEDBACK_RECORDS, METRIC_CONTEXT, TREND_POINTS, type FeedbackRecordMock } from "@/mocks/observability";

export default function FeedbackAdminPage() {
  const [records, setRecords] = useState(() => FEEDBACK_RECORDS.map((item) => ({ ...item, history: [...item.history] })));
  const [selectedId, setSelectedId] = useState(records[0]?.id ?? "");
  const [rating, setRating] = useState("all");
  const [status, setStatus] = useState("all");
  const [query, setQuery] = useState("");
  const selected = records.find((record) => record.id === selectedId) ?? records[0];

  const filtered = useMemo(() => records.filter((record) => {
    if (rating !== "all" && record.rating !== rating) return false;
    if (status !== "all" && record.status !== status) return false;
    const normalized = query.trim().toLowerCase();
    return !normalized || `${record.user} ${record.agent} ${record.reason} ${record.comment}`.toLowerCase().includes(normalized);
  }), [query, rating, records, status]);

  function updateRecord(next: FeedbackRecordMock) {
    setRecords((current) => current.map((record) => record.id === next.id ? next : record));
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-app-bg">
      <Topbar title="用户反馈" badge={{ label: "管理员视图", tone: "info" }} actions={<MockDataBadge />} />
      <div className="min-h-0 flex-1 overflow-y-auto p-4 md:p-5">
        <div className="mx-auto max-w-[1500px] space-y-4">
          <div className="flex flex-wrap items-center gap-2 text-[10.5px] text-ink-subtle">
            <span>口径：有效 Feedback / 可反馈回答</span><span>·</span><span>{METRIC_CONTEXT.version}</span><span>·</span><span>{METRIC_CONTEXT.window}</span><span className="ml-auto">更新于 {METRIC_CONTEXT.asOf}</span>
          </div>

          <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="反馈核心指标">
            <MetricCard label="反馈率" value="24.6%" change="+2.9%" hint="3,159 / 12,842" icon={<MessageSquareText />} />
            <MetricCard label="点赞率（Feedback）" value="89.4%" change="+3.2%" hint="点赞 / 有效 Feedback" icon={<ThumbsUp />} tone="green" />
            <MetricCard label="点赞率（回答）" value="22.0%" change="+1.8%" hint="点赞 / 可反馈回答" icon={<CheckCircle2 />} tone="slate" />
            <MetricCard label="待处理负评" value="37" change="-12.4%" hint="其中高优先级 6 条" icon={<ThumbsDown />} tone="red" />
          </section>

          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.65fr)_minmax(300px,0.8fr)]">
            <SectionCard title="反馈与满意度趋势" description="按回答发生日统计；修改评价不会重复计数">
              <LineChart values={TREND_POINTS.map((point) => point.feedbackRate)} secondaryValues={TREND_POINTS.map((point) => point.positiveRate)} labels={TREND_POINTS.map((point) => point.label)} primaryLabel="反馈率" secondaryLabel="点赞率" />
            </SectionCard>
            <SectionCard title="点踩原因分布" description="当前筛选范围内的负面原因">
              <div className="space-y-3.5 p-4">{FEEDBACK_REASON_DISTRIBUTION.map((item, index) => <BarMeter key={item.label} label={item.label} value={item.value} tone={index === 0 ? "red" : index < 3 ? "amber" : "blue"} />)}</div>
            </SectionCard>
          </div>

          <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1.65fr)_minmax(330px,0.75fr)]">
            <SectionCard
              title="Feedback 管理"
              description={`${filtered.length} 条 Mock 记录 · 服务端分页将在后端接口完成后接入`}
              actions={<div className="flex flex-wrap items-center gap-2"><label className="flex h-8 items-center gap-2 rounded-lg border border-line bg-app-soft px-2.5"><Search className="h-3.5 w-3.5 text-ink-subtle" /><input aria-label="搜索反馈" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="用户、Agent 或内容" className="w-36 bg-transparent text-[11px] outline-none" /></label><select aria-label="评价筛选" value={rating} onChange={(event) => setRating(event.target.value)} className="h-8 rounded-lg border border-line bg-white px-2 text-[11px]"><option value="all">全部评价</option><option value="up">点赞</option><option value="down">点踩</option></select><select aria-label="状态筛选" value={status} onChange={(event) => setStatus(event.target.value)} className="h-8 rounded-lg border border-line bg-white px-2 text-[11px]"><option value="all">全部状态</option><option>待处理</option><option>处理中</option><option>已解决</option><option>已关闭</option></select></div>}
            >
              <div className="overflow-x-auto">
                <table className="w-full min-w-[820px] text-left text-[11.5px]">
                  <thead className="bg-app-soft text-ink-muted"><tr><th className="px-4 py-2.5 font-semibold">评价</th><th className="px-3 py-2.5 font-semibold">用户 / 内容</th><th className="px-3 py-2.5 font-semibold">Agent</th><th className="px-3 py-2.5 font-semibold">原因</th><th className="px-3 py-2.5 font-semibold">Case</th><th className="px-4 py-2.5 font-semibold">时间</th></tr></thead>
                  <tbody className="divide-y divide-line">
                    {filtered.map((record) => (
                      <tr key={record.id} className={classNames("cursor-pointer hover:bg-app-soft/70", selected?.id === record.id && "bg-accent-soft/60")} onClick={() => setSelectedId(record.id)}>
                        <td className="px-4 py-3">{record.rating === "up" ? <ThumbsUp className="h-4 w-4 text-success" /> : <ThumbsDown className="h-4 w-4 text-danger" />}</td>
                        <td className="max-w-[300px] px-3 py-3"><div className="font-semibold text-ink">{record.user}</div><button type="button" onClick={() => setSelectedId(record.id)} className="mt-0.5 block max-w-full truncate text-left text-[10.5px] text-ink-muted hover:text-accent">{record.comment}</button></td>
                        <td className="px-3 py-3"><div className="font-semibold text-ink">{record.agent}</div><div className="text-[10.5px] text-ink-subtle">{record.version}</div></td>
                        <td className="px-3 py-3 text-ink-muted">{record.reason}</td>
                        <td className="px-3 py-3"><StatusPill status={record.status} /><div className="mt-1 text-[10px] text-ink-subtle">{record.assignee}</div></td>
                        <td className="px-4 py-3 text-ink-muted">{record.createdAt.slice(5)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </SectionCard>

            {selected ? <FeedbackDetail record={selected} onUpdate={updateRecord} /> : null}
          </div>
        </div>
      </div>
    </div>
  );
}

function FeedbackDetail({ record, onUpdate }: { record: FeedbackRecordMock; onUpdate: (record: FeedbackRecordMock) => void }) {
  const [status, setStatus] = useState(record.status);
  const [assignee, setAssignee] = useState(record.assignee);
  const [conclusion, setConclusion] = useState(record.conclusion);

  const currentKey = `${record.id}:${record.status}:${record.assignee}:${record.conclusion}`;
  useEffect(() => {
    setStatus(record.status);
    setAssignee(record.assignee);
    setConclusion(record.conclusion);
  }, [currentKey, record.assignee, record.conclusion, record.status]);

  function save() {
    const action = `状态更新为${status}，负责人：${assignee}`;
    onUpdate({ ...record, status, assignee, conclusion, history: [...record.history, { at: "刚刚", actor: "周然", action }] });
  }

  return (
    <SectionCard title="Feedback 详情" description={record.id} className="xl:sticky xl:top-0">
      <div className="space-y-4 p-4">
        <div className="rounded-lg border border-line bg-app-soft p-3">
          <div className="flex items-center gap-2"><span className={classNames("flex h-7 w-7 items-center justify-center rounded-lg", record.rating === "up" ? "bg-success-soft text-success" : "bg-danger-soft text-danger")}>{record.rating === "up" ? <ThumbsUp className="h-3.5 w-3.5" /> : <ThumbsDown className="h-3.5 w-3.5" />}</span><div><strong className="text-[12px] text-ink">{record.user}</strong><p className="text-[10.5px] text-ink-subtle">{record.agent} {record.version}</p></div><StatusPill status={record.status} /></div>
          <p className="mt-3 text-[12px] leading-5 text-ink">{record.comment}</p>
          <p className="mt-2 text-[10.5px] text-ink-muted">原因：{record.reason}</p>
        </div>

        <div className="grid grid-cols-2 gap-2 text-[10.5px]">
          <Link to={`/chat/${record.conversationId}`} className="rounded-lg border border-line px-2.5 py-2 font-semibold text-accent hover:bg-accent-soft">返回原对话</Link>
          {record.traceId ? <Link to={`/admin/observability?trace=${record.traceId}`} className="rounded-lg border border-line px-2.5 py-2 font-semibold text-accent hover:bg-accent-soft">回溯 Trace</Link> : <span className="rounded-lg border border-dashed border-line px-2.5 py-2 text-ink-subtle">Trace 已过期</span>}
        </div>

        {record.rating === "down" ? <div className="space-y-3 border-t border-line pt-4"><div className="grid grid-cols-2 gap-2"><label className="text-[10.5px] font-semibold text-ink-muted">状态<select aria-label="Case 状态" value={status} onChange={(event) => setStatus(event.target.value as FeedbackRecordMock["status"])} className="mt-1 h-9 w-full rounded-lg border border-line bg-white px-2 text-[11.5px] text-ink"><option>待处理</option><option>处理中</option><option>已解决</option><option>已关闭</option></select></label><label className="text-[10.5px] font-semibold text-ink-muted">负责人<select aria-label="Case 负责人" value={assignee} onChange={(event) => setAssignee(event.target.value)} className="mt-1 h-9 w-full rounded-lg border border-line bg-white px-2 text-[11.5px] text-ink"><option>未分配</option><option>王珂</option><option>李牧</option><option>周然</option></select></label></div><label className="block text-[10.5px] font-semibold text-ink-muted">处理结论<textarea aria-label="处理结论" value={conclusion} onChange={(event) => setConclusion(event.target.value)} rows={3} placeholder="记录原因、修复版本或后续动作" className="mt-1 w-full resize-none rounded-lg border border-line px-2.5 py-2 text-[11.5px] font-normal text-ink outline-none focus:border-accent" /></label><button type="button" onClick={save} className="btn-primary w-full"><UserRoundCheck className="h-4 w-4" />保存处理结果</button></div> : null}

        <div className="border-t border-line pt-4"><h3 className="text-[11.5px] font-bold text-ink">操作历史</h3><div className="mt-3 space-y-3">{record.history.map((item, index) => <div key={`${item.at}-${index}`} className="flex gap-2.5"><span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-app-soft text-ink-subtle"><Clock3 className="h-3 w-3" /></span><div><p className="text-[10.5px] leading-4 text-ink">{item.action}</p><p className="mt-0.5 text-[9.5px] text-ink-subtle">{item.actor} · {item.at}</p></div></div>)}</div></div>
      </div>
    </SectionCard>
  );
}

function StatusPill({ status }: { status: FeedbackRecordMock["status"] }) {
  return <span className={classNames("ml-auto inline-flex rounded-full px-2 py-0.5 text-[9.5px] font-bold", status === "待处理" ? "bg-danger-soft text-danger" : status === "处理中" ? "bg-warning-soft text-warning-deep" : status === "已解决" ? "bg-success-soft text-success-deep" : "bg-app-soft text-ink-muted")}>{status}</span>;
}
