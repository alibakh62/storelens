import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import { Lock, X } from "lucide-react";
import { useEffect } from "react";
import { api } from "../lib/api";
import { cls, localTime } from "../lib/format";
import { useApp } from "../lib/store";
import { PirButton } from "./Pir";
import { CitationChip, CitedText, ErrorState, Skeleton } from "./ui";

function Shell({ title, onClose, children, testid }: { title: React.ReactNode; onClose: () => void; children: React.ReactNode; testid?: string }) {
  return (
    <motion.aside initial={{ x: 40, opacity: 0 }} animate={{ x: 0, opacity: 1 }} exit={{ x: 40, opacity: 0 }} transition={{ duration: 0.2 }}
      data-testid={testid} className="fixed bottom-0 right-0 top-16 z-40 flex w-[560px] max-w-[95vw] flex-col border-l border-border bg-surface shadow-2xl">
      <div className="flex items-center justify-between border-b border-border px-4 py-3"><div className="font-semibold">{title}</div>
        <button onClick={onClose} className="rounded p-1 text-muted hover:bg-surface2" aria-label="Close drawer"><X size={16} /></button></div>
      <div className="flex-1 overflow-auto p-4 scroll-thin">{children}</div>
    </motion.aside>
  );
}

const SKIP = new Set(["work_notes", "citations", "sys_id", "restricted"]);
const val = (v: any) => {
  if (v === null || v === undefined || v === "") return <span className="text-muted">—</span>;
  if (v === "•••• (restricted)") return <span className="inline-flex items-center gap-1 text-muted"><Lock size={11} /> restricted</span>;
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}T/.test(v)) return localTime(v, -6, true);
  if (typeof v === "string" && /^(INC|CHG|PRB|KB)\d{7}$/.test(v)) return <CitationChip id={v} />;
  if (Array.isArray(v)) return v.slice(0, 30).join(", ") + (v.length > 30 ? "…" : "");
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
};

function RecordDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const { persona } = useApp();
  const q = useQuery({ queryKey: ["record", id, persona?.user_id], queryFn: () => api(`/api/records/${id}`) });
  const r = q.data?.record;
  return (
    <Shell title={<span className="font-mono">{id}</span>} onClose={onClose} testid="record-drawer">
      {q.isLoading ? <Skeleton className="h-96" /> : q.error ? <ErrorState error={q.error} /> : (
        <div className="space-y-4">
          <div><div className="label">{q.data.kind}</div><div className="text-base font-semibold leading-snug">{r.short_description}</div>
            {r.major_incident && <div className="mt-2"><PirButton num={id} /></div>}</div>
          <dl className="grid grid-cols-[150px_1fr] gap-x-3 gap-y-1.5 text-xs">
            {Object.entries(r).filter(([k]) => !SKIP.has(k)).map(([k, v]) => (
              <div key={k} className="contents"><dt className="text-muted">{k.replace(/_/g, " ")}</dt><dd className="break-words">{val(v)}</dd></div>))}
          </dl>
          {r.work_notes && (
            <div><div className="label mb-1">Work notes ({r.work_notes.length})</div>
              <ol className="space-y-2">{r.work_notes.map((n: any, i: number) => (
                <li key={i} className={cls("rounded-lg border p-2 text-xs", n.entry_type === "system" ? "border-border bg-surface2/40" : "border-border")}>
                  <div className="mb-0.5 flex gap-2 text-[11px] text-muted"><span>{localTime(n.ts, -6, true)}</span><span>{n.author}</span><span className="chip">{n.entry_type}</span></div>
                  <CitedText text={n.text} /></li>))}</ol></div>)}
        </div>)}
    </Shell>
  );
}

function TraceDrawer({ turnId, onClose }: { turnId?: string; onClose: () => void }) {
  const { turns } = useApp();
  const t = (turnId ? turns.find((x) => x.id === turnId) : null) ?? turns[turns.length - 1];
  const tr = t?.trace, v = t?.validation;
  const mode = tr?.provider_mode;
  const badge = mode === "template" ? "Offline" : mode === "replay" ? "Replay" : mode === "scope" ? "Scope" : "Live";
  return (
    <Shell title="Trace · Responsible AI" onClose={onClose} testid="trace-drawer">
      {!t ? <div className="text-sm text-muted">Ask a question to see its trace.</div> : (
        <div className="space-y-4 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <span className="chip" data-testid="mode-badge">mode: {badge}</span>
            {tr && <span className="chip">{Math.round(tr.latency_ms)} ms total</span>}
            {tr && <span className={cls("chip", tr.masked_fields_total ? "border-warn/50 text-warn" : "")}>masked fields: {tr.masked_fields_total}</span>}
            {tr?.usage?.in > 0 && <span className="chip">tokens {tr.usage.in} in / {tr.usage.out} out</span>}
          </div>
          <div className="rounded-lg border border-border p-2"><div className="label mb-1">Question</div>{t.question}</div>
          <div><div className="label mb-1">Tool calls</div>
            <table className="w-full"><thead className="text-muted"><tr><th className="text-left font-normal">tool</th><th className="text-left font-normal">args</th><th className="font-normal">ms</th><th className="font-normal">rows</th><th className="font-normal">masked</th></tr></thead>
              <tbody>{(tr?.tools ?? []).map((x: any, i: number) => (
                <tr key={i} className="border-t border-border align-top"><td className="py-1 font-mono">{x.name}</td><td className="break-all font-mono text-muted">{JSON.stringify(x.args)}</td>
                  <td className="text-center tabular">{Math.round(x.ms)}</td><td className="text-center tabular">{x.rows}</td><td className={cls("text-center tabular", x.masked_fields && "text-warn")}>{x.masked_fields}</td></tr>))}</tbody></table></div>
          {v && (
            <div><div className="label mb-1">Validation</div>
              <div className="grid grid-cols-2 gap-2">
                <div className="rounded border border-border p-2">citations checked <b className="tabular">{v.citations_checked}</b></div>
                <div className="rounded border border-border p-2">bullets removed <b className="tabular">{v.removed_count}</b></div>
                <div className="rounded border border-border p-2">ungrounded numbers <b className="tabular">{v.ungrounded_count}</b></div>
                <div className={cls("rounded border p-2", v.rbac_redactions ? "border-crit text-crit" : "border-border")}>RBAC redactions <b className="tabular">{v.rbac_redactions}</b></div>
              </div>
              {v.removed_bullets?.length > 0 && <ul className="mt-2 space-y-1">{v.removed_bullets.map((b: any, i: number) => <li key={i} className="rounded border border-warn/40 p-1.5 text-muted"><b className="text-warn">{b.reason}</b> — {b.text}</li>)}</ul>}
              {v.intent && <div className="mt-2 text-muted">template intent: <span className="font-mono">{v.intent}</span></div>}
            </div>)}
          {tr?.fallback_reason && <div className="text-muted">fallback reason: {tr.fallback_reason}</div>}
          <div className="text-[11px] text-muted">Read-only by design. RBAC masking happens in the data layer before the model sees anything; every answer is citation- and number-checked before it is shown.</div>
        </div>)}
    </Shell>
  );
}

export function Drawers() {
  const { drawer, setDrawer } = useApp();
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") setDrawer(null); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [setDrawer]);
  return (
    <AnimatePresence>
      {drawer?.kind === "record" && <RecordDrawer key={drawer.id} id={drawer.id} onClose={() => setDrawer(null)} />}
      {drawer?.kind === "trace" && <TraceDrawer key="trace" turnId={drawer.turnId} onClose={() => setDrawer(null)} />}
    </AnimatePresence>
  );
}
