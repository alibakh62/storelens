import { useMutation, useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Copy, Download, Loader2, MessageSquareShare, Play } from "lucide-react";
import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { download } from "../components/Pir";
import { Modal, TeamsFrame, Toast } from "../components/Teams";
import { Card, CitationChip, CitedText, Empty, ErrorState, PriorityPill, Skeleton, Ticker } from "../components/ui";
import { api } from "../lib/api";
import { cls, localTime, money } from "../lib/format";
import { useApp } from "../lib/store";

export function OpsPage() {
  const { persona, meta } = useApp();
  const ov = useQuery({ queryKey: ["overview", persona?.user_id], queryFn: () => api("/api/fleet/overview"), refetchInterval: 20000 });
  const scoped = persona?.region_scope && persona.region_scope !== "All";
  const [region, setRegion] = useState<string>(scoped ? persona!.region_scope : "Texas");
  useEffect(() => { if (scoped) setRegion(persona!.region_scope); }, [scoped, persona]);
  return (
    <div className="space-y-4 p-4">
      <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">
        {ov.isLoading ? Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-28" />) : ov.data?.tiles.map((t: any) => (
          <button key={t.region} onClick={() => !scoped && setRegion(t.region)} className={cls("card p-3 text-left", region === t.region && "border-info")}>
            <div className="flex items-center justify-between"><span className="font-medium">{t.region}</span>{t.majors > 0 && <span className="chip border-crit/50 text-crit">major</span>}</div>
            <div className="mt-1 text-2xl font-semibold tabular">{t.open}<span className="ml-1 text-xs font-normal text-muted">open</span></div>
            <div className="mt-1 flex flex-wrap gap-1 text-[11px]">{Object.entries(t.by_priority).map(([k, v]: any) => <span key={k} className="chip">{k} {v}</span>)}</div>
            <div className={cls("mt-1 text-xs", t.sla_at_risk ? "text-warn" : "text-muted")}>{t.sla_at_risk} at SLA risk</div>
          </button>))}
      </div>
      <div className="grid gap-4 xl:grid-cols-12">
        <div className="xl:col-span-5">
          <Card title="Open major incidents">
            {(ov.data?.majors ?? []).length === 0 ? <Empty>No open majors.</Empty> : ov.data.majors.map((m: any) => (
              <div key={m.number} className="mb-2 rounded-lg border border-crit/40 bg-crit/5 p-3">
                <div className="flex items-center gap-2"><PriorityPill p={1} /><CitationChip id={m.number} /><span className="ml-auto"><Ticker base={m.usd_so_far} perHour={m.per_hour_now} k={m.as_of} className="text-lg" /></span></div>
                <div className="mt-1 text-sm font-medium">{m.short_description.replace("MAJOR: ", "")}</div>
                <div className="text-xs text-muted">{m.stores} stores · {m.offline} offline · opened {localTime(m.opened_at)}</div>
              </div>))}
          </Card>
        </div>
        <div className="xl:col-span-7"><Handoff region={region} setRegion={setRegion} regions={meta?.regions ?? []} locked={!!scoped} /></div>
      </div>
    </div>
  );
}

function Handoff({ region, setRegion, regions, locked }: { region: string; setRegion: (r: string) => void; regions: string[]; locked: boolean }) {
  const { persona } = useApp();
  const q = useQuery({ queryKey: ["handoff", region, persona?.user_id], queryFn: () => api(`/api/handoff?region=${encodeURIComponent(region)}`) });
  const [teams, setTeams] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const s = q.data?.sections;
  return (
    <Card title="Shift handoff" right={
      <select data-testid="handoff-region" value={region} disabled={locked} onChange={(e) => setRegion(e.target.value)} className="rounded-md border border-border bg-surface2 px-2 py-1 text-sm">
        {["All", ...regions].map((r) => <option key={r}>{r}</option>)}</select>}>
      {q.isLoading ? <Skeleton className="h-64" /> : q.error ? <ErrorState error={q.error} retry={() => q.refetch()} /> : (
        <div className="space-y-3" data-testid="handoff">
          <div className="rounded-lg border border-info/40 bg-info/5 p-3 text-sm leading-relaxed"><div className="label mb-1">Digest</div><CitedText text={q.data.digest} /></div>
          <div className="flex flex-wrap gap-2">
            <button className="btn-primary btn" data-testid="preview-teams" onClick={() => setTeams(true)}><MessageSquareShare size={14} /> Preview Teams card</button>
            <button className="btn" onClick={() => { navigator.clipboard?.writeText(q.data.digest); setToast("Copied"); }}><Copy size={14} /> Copy</button>
            <button className="btn" onClick={() => download(`handoff_${region}.md`, q.data.markdown)}><Download size={14} /> Download .md</button>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <Section title={`P1/P2 open (${s.p1_p2_open_count})`} rows={s.p1_p2_open} />
            <Section title={`SLA breached / < 2h (${s.sla_risk_count})`} rows={s.sla_risk} />
            <Section title={`On hold > 24h (${s.on_hold_24h_count})`} rows={s.on_hold_24h} />
            <div className="rounded-lg border border-border p-2.5 text-xs"><div className="label mb-1">Changes next 24h</div>
              {s.changes_next_24h.length ? s.changes_next_24h.map((c: any) => <div key={c.number} className="mb-1"><CitationChip id={c.number} /> {c.short_description} · {localTime(c.planned_start)}</div>) : <span className="text-muted">None</span>}</div>
            <div className="rounded-lg border border-border p-2.5 text-xs"><div className="label mb-1">New chronic flags (7 days)</div>
              {s.new_chronic.slice(0, 5).map((c: any) => <div key={c.unit}>Store {c.store_id} · {c.unit} · {c.count_365d} failures</div>)}</div>
            <div className="rounded-lg border border-border p-2.5 text-xs"><div className="label mb-1">Notable resolutions (12h)</div>
              {s.notable_resolutions.length ? s.notable_resolutions.map((r: any) => <div key={r.number} className="mb-1"><CitationChip id={r.number} /> {r.short_description}</div>) : <span className="text-muted">None</span>}</div>
          </div>
          {teams && <Modal title="Preview in Teams" onClose={() => setTeams(false)}><TeamsFrame card={q.data.adaptive_card} channel={`store-ops-${region.toLowerCase()}`} /></Modal>}
          {toast && <Toast text={toast} onDone={() => setToast(null)} />}
        </div>)}
    </Card>
  );
}

function Section({ title, rows }: { title: string; rows: any[] }) {
  return (
    <div className="rounded-lg border border-border p-2.5 text-xs"><div className="label mb-1">{title}</div>
      {rows.length === 0 ? <span className="text-muted">None</span> : rows.slice(0, 6).map((r) => (
        <div key={r.number} className="mb-1 flex items-center gap-1.5"><PriorityPill p={r.priority} /><CitationChip id={r.number} /><span className="truncate">{r.store_id ? `${r.store_id} · ` : ""}{r.short_description}</span></div>))}
    </div>
  );
}

// ---------------------------------------------------------------- fleet insights
const NLQ_EX = ["Which stores had the most repeat incidents in the last 90 days?", "MTTR by region and category", "7NOW incidents trend by week",
  "Incidents per ISP for network categories", "SLA breach rate by assignment group", "Top stores by lost sales"];

export function FleetPage() {
  const { persona, setStoreId } = useApp();
  const ch = useQuery({ queryKey: ["chronic", persona?.user_id], queryFn: () => api("/api/fleet/chronic") });
  const [sortCost, setSortCost] = useState(true);
  const items = [...(ch.data?.items ?? [])].sort((a, b) => sortCost ? (b.cost_365d_usd ?? 0) - (a.cost_365d_usd ?? 0) : b.count_365d - a.count_365d);
  return (
    <div className="grid gap-4 p-4 xl:grid-cols-12">
      <div className="xl:col-span-6">
        <Card title="Chronic devices" right={<button className="btn text-xs" onClick={() => setSortCost((s) => !s)}>sort: {sortCost ? "cost" : "count"}</button>}>
          {ch.isLoading ? <Skeleton className="h-96" /> : ch.error ? <ErrorState error={ch.error} /> : (<>
            <div className="mb-2 text-sm">{ch.data.summary}</div>
            <div className="max-h-[640px] overflow-auto scroll-thin">
              <table className="w-full text-xs" data-testid="chronic-table"><thead className="sticky top-0 bg-surface text-muted"><tr><th className="py-1 text-left font-normal">Store</th><th className="text-left font-normal">Device</th><th className="font-normal">365d</th><th className="font-normal">90d</th><th className="text-right font-normal">Cost 12m</th><th className="text-left font-normal">Recommendation</th></tr></thead>
                <tbody>{items.map((c) => (
                  <tr key={c.unit} className="border-t border-border align-top">
                    <td className="py-1.5"><a className="font-mono text-info hover:underline" href={`/stores/${c.store_id}`} onClick={() => setStoreId(c.store_id)}>{c.store_id}</a><div className="text-muted">{c.city}</div></td>
                    <td className="font-mono">{c.unit}</td><td className="text-center tabular">{c.count_365d}</td><td className="text-center tabular">{c.count_90d}</td>
                    <td className="text-right tabular text-money">{c.cost_365d_usd === null ? "—" : money(c.cost_365d_usd)}</td>
                    <td className="pl-2 text-muted">{c.recommendation}{c.problem && <> <CitationChip id={c.problem} /></>}</td></tr>))}</tbody></table></div>
          </>)}
        </Card>
      </div>
      <div className="xl:col-span-6"><NLQ /></div>
    </div>
  );
}

function NLQ() {
  const [q, setQ] = useState("");
  const [showSql, setShowSql] = useState(true);
  const m = useMutation({ mutationFn: (question: string) => api("/api/nlq", { method: "POST", body: JSON.stringify({ question }) }) });
  const r = m.data;
  const rows = (r?.rows ?? []).map((row: any[]) => Object.fromEntries(r.columns.map((c: string, i: number) => [c, row[i]])));
  const cs = r?.chart_spec;
  const csv = () => download("storelens_query.csv", [r.columns.join(","), ...r.rows.map((x: any[]) => x.map((v) => JSON.stringify(v ?? "")).join(","))].join("\n"), "text/csv");
  return (
    <Card title="NL analytics">
      <form onSubmit={(e) => { e.preventDefault(); if (q.trim()) m.mutate(q.trim()); }} className="flex gap-2">
        <input data-testid="nlq-input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask an analytics question…" className="flex-1 rounded-lg border border-border bg-surface2/50 px-3 py-2 text-sm outline-none focus:border-info" />
        <button className="btn-primary btn" disabled={m.isPending}>{m.isPending ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />} Run</button>
      </form>
      <div className="mt-2 flex flex-wrap gap-1.5">{NLQ_EX.map((x) => <button key={x} className="btn text-xs" onClick={() => { setQ(x); m.mutate(x); }}>{x}</button>)}</div>
      {m.error && <div className="mt-3"><ErrorState error={m.error} /></div>}
      {r && (
        <div className="mt-3 space-y-3" data-testid="nlq-result">
          {!r.ok ? <div className="text-sm text-warn">{r.error}</div> : (<>
            <div className="rounded-lg border border-border">
              <button className="flex w-full items-center gap-1 px-3 py-2 text-xs text-muted" onClick={() => setShowSql((s) => !s)}>{showSql ? <ChevronDown size={13} /> : <ChevronRight size={13} />} SQL (validated, read-only{r.mode === "template" ? ", offline template" : ""})</button>
              {showSql && <pre className="overflow-auto border-t border-border px-3 py-2 font-mono text-[11px] leading-relaxed text-info scroll-thin whitespace-pre-wrap">{r.sql}</pre>}
            </div>
            {cs?.type !== "table" && rows.length > 0 && (
              <div className="h-56"><ResponsiveContainer>{cs.type === "line" ? (
                <LineChart data={rows.map((x: any) => ({ ...x, [cs.x]: String(x[cs.x]).slice(0, 10) }))}><CartesianGrid stroke="rgb(var(--border))" strokeDasharray="3 3" /><XAxis dataKey={cs.x} tick={{ fontSize: 10, fill: "rgb(var(--muted))" }} /><YAxis tick={{ fontSize: 10, fill: "rgb(var(--muted))" }} /><Tooltip contentStyle={{ background: "rgb(var(--surface))", border: "1px solid rgb(var(--border))", fontSize: 12 }} /><Line dataKey={cs.y} stroke="rgb(var(--info))" dot={false} strokeWidth={2} /></LineChart>) : (
                <BarChart data={rows.slice(0, 25)}><CartesianGrid stroke="rgb(var(--border))" strokeDasharray="3 3" /><XAxis dataKey={cs.x} tick={{ fontSize: 10, fill: "rgb(var(--muted))" }} interval={0} angle={-30} textAnchor="end" height={60} /><YAxis tick={{ fontSize: 10, fill: "rgb(var(--muted))" }} /><Tooltip contentStyle={{ background: "rgb(var(--surface))", border: "1px solid rgb(var(--border))", fontSize: 12 }} /><Bar dataKey={cs.y} fill="rgb(var(--info))" radius={3} /></BarChart>)}
              </ResponsiveContainer></div>)}
            <div className="flex items-center justify-between text-xs text-muted"><span>{r.row_count} rows</span><button className="btn text-xs" onClick={csv}><Download size={13} /> CSV</button></div>
            <div className="max-h-72 overflow-auto rounded-lg border border-border scroll-thin">
              <table className="w-full text-xs"><thead className="sticky top-0 bg-surface2 text-muted"><tr>{r.columns.map((c: string) => <th key={c} className="px-2 py-1 text-left font-normal">{c}</th>)}</tr></thead>
                <tbody>{r.rows.slice(0, 100).map((row: any[], i: number) => <tr key={i} className="border-t border-border">{row.map((v, j) => <td key={j} className="px-2 py-1 tabular">{typeof v === "number" ? (Number.isInteger(v) ? v : v.toFixed(2)) : String(v ?? "—").slice(0, 40)}</td>)}</tr>)}</tbody></table></div>
          </>)}
        </div>)}
    </Card>
  );
}
