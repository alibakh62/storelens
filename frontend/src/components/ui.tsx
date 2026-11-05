import { useQuery } from "@tanstack/react-query";
import * as Tooltip from "@radix-ui/react-tooltip";
import { AlertOctagon, AlertTriangle, CheckCircle2, Info, Lock, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";
import { api } from "../lib/api";
import { cls, localTime, money } from "../lib/format";
import { useApp, useTicker } from "../lib/store";

export const STATUS = {
  critical: { color: "text-crit", bg: "bg-crit/10", border: "border-crit/40", icon: AlertOctagon, label: "Critical" },
  watch: { color: "text-warn", bg: "bg-warn/10", border: "border-warn/40", icon: AlertTriangle, label: "Watch" },
  healthy: { color: "text-accent", bg: "bg-accent/10", border: "border-accent/40", icon: CheckCircle2, label: "Healthy" },
  info: { color: "text-info", bg: "bg-info/10", border: "border-info/40", icon: Info, label: "Info" },
} as const;

export function StatusBadge({ status, label }: { status: keyof typeof STATUS; label?: string }) {
  const s = STATUS[status] ?? STATUS.info;
  const I = s.icon;
  return (
    <span className={cls("inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium", s.color, s.border, s.bg)}>
      <I size={13} aria-hidden /> {label ?? s.label}
    </span>
  );
}

export function PriorityPill({ p }: { p: string | number }) {
  const n = typeof p === "number" ? p : parseInt(String(p)[0] || "5");
  const c = n === 1 ? "bg-crit text-white" : n === 2 ? "bg-warn text-black" : n === 3 ? "bg-info/80 text-white" : "bg-surface2 text-muted border border-border";
  return <span className={cls("inline-flex h-5 min-w-[26px] items-center justify-center rounded px-1 text-[11px] font-semibold font-mono", c)}>P{n}</span>;
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={cls("animate-pulse rounded-md bg-surface2", className)} />;
}

export function ErrorState({ error, retry }: { error: any; retry?: () => void }) {
  const restricted = error?.status === 403;
  return (
    <div className="flex flex-col items-start gap-2 rounded-lg border border-border bg-surface2/50 p-4 text-sm">
      <div className="flex items-center gap-2 text-muted">{restricted ? <Lock size={15} /> : <AlertTriangle size={15} />}
        {String(error?.message ?? error)}</div>
      {retry && !restricted && <button className="btn" onClick={retry}><RefreshCw size={14} /> Retry</button>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-dashed border-border p-4 text-center text-sm text-muted">{children}</div>;
}

export function Card({ title, right, children, className = "" }: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cls("card p-4", className)}>
      {(title || right) && <div className="mb-3 flex items-center justify-between gap-2">{title && <h3 className="label">{title}</h3>}{right}</div>}
      {children}
    </section>
  );
}

export function Restricted({ what = "Restricted for your role" }: { what?: string }) {
  return <span className="inline-flex items-center gap-1 text-muted" title={what}><Lock size={12} aria-hidden /> <span className="text-xs">restricted</span></span>;
}

export function Money({ v, className = "" }: { v: number | null | undefined; className?: string }) {
  if (v === null || v === undefined) return <Restricted />;
  return <span className={cls("tabular text-money", className)}>{money(v)}</span>;
}

export function Ticker({ base, perHour, k, className = "", compact = true }: { base: number | null | undefined; perHour?: number | null; k?: any; className?: string; compact?: boolean }) {
  const v = useTicker(base, perHour, k);
  if (v === null || v === undefined) return <Restricted />;
  return <span className={cls("tabular text-money font-semibold", className)}>{money(v, { compact })}</span>;
}

export function Footnote() {
  return <span className="cursor-help text-muted" title="Estimate: hourly sales baseline × service loss profile. Gross sales, not margin.">ⓘ</span>;
}

export function CitationChip({ id }: { id: string }) {
  const { setDrawer } = useApp();
  return (
    <Tooltip.Root delayDuration={150}>
      <Tooltip.Trigger asChild>
        <button onClick={() => setDrawer({ kind: "record", id })}
          className="mx-0.5 inline-flex items-center rounded border border-info/40 bg-info/10 px-1.5 py-[1px] align-baseline font-mono text-[11px] text-info hover:bg-info/20 focus:outline-none focus-visible:ring-2 focus-visible:ring-info"
          aria-label={`Open ${id}`}>{id}</button>
      </Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content sideOffset={6} className="z-50 w-80 rounded-lg border border-border bg-surface p-3 text-sm shadow-xl">
          <RecordPeek id={id} />
          <Tooltip.Arrow className="fill-border" />
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}

function RecordPeek({ id }: { id: string }) {
  const { data, isLoading, error } = useQuery({ queryKey: ["record", id], queryFn: () => api(`/api/records/${id}`), staleTime: 60_000 });
  if (isLoading) return <Skeleton className="h-16" />;
  if (error) return <div className="text-muted">{String((error as any).message)}</div>;
  const r = data.record;
  const title = r.short_description;
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between"><span className="font-mono text-xs text-info">{id}</span><span className="chip">{r.state ?? r.workflow_state ?? data.kind}</span></div>
      <div className="font-medium leading-snug">{title}</div>
      <div className="text-xs text-muted">
        {data.kind === "incident" && <>Store {r.store_id ?? "multiple"} · opened {localTime(r.opened_at, -6, true)}{r.resolved_at ? ` · resolved ${localTime(r.resolved_at, -6, true)}` : ""}</>}
        {data.kind === "change" && <>{r.scope} · {r.actual_start ? `started ${localTime(r.actual_start, -6, true)}` : `planned ${localTime(r.planned_start, -6, true)}`}</>}
        {data.kind === "problem" && <>{r.known_error ? "Known error · " : ""}{r.assignment_group}</>}
        {data.kind === "kb" && <>{r.kb_type} · {r.category}</>}
      </div>
      <div className="text-[11px] text-muted">Click to open the record</div>
    </div>
  );
}

/** Render text with inline INC/CHG/PRB/KB ids turned into citation chips. */
export function CitedText({ text }: { text: string }) {
  const parts: ReactNode[] = [];
  let last = 0;
  const rx = /\b(?:INC|CHG|PRB|KB)\d{7}\b/g;
  let m: RegExpExecArray | null;
  while ((m = rx.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    parts.push(<CitationChip key={m.index} id={m[0]} />);
    last = m.index + m[0].length;
  }
  parts.push(text.slice(last));
  return <>{parts}</>;
}

export function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-surface2/60 p-2.5">
      <div className="label">{label}</div>
      <div className="mt-0.5 text-lg font-semibold tabular">{value}</div>
      {sub && <div className="text-xs text-muted">{sub}</div>}
    </div>
  );
}
