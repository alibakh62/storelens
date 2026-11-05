import * as Tabs from "@radix-ui/react-tabs";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, FileText, Link2Off, MapPin, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis } from "recharts";
import { api } from "../lib/api";
import { cls, hours, localTime, money, pct } from "../lib/format";
import { useApp } from "../lib/store";
import { BlastMap } from "./BlastMap";
import { PirButton } from "./Pir";
import { CitationChip, CitedText, Empty, ErrorState, Footnote, Skeleton, Ticker } from "./ui";

const TABS = [["timeline", "Timeline"], ["fixes", "Fixes"], ["changes", "Changes"], ["blast", "Blast radius"], ["impact", "Impact"]];

export function useEvidenceContext(storeId: number) {
  const { persona, incident, blastAnchor } = useApp();
  const h = useQuery({ queryKey: ["health", storeId, persona?.user_id], queryFn: () => api(`/api/stores/${storeId}/health`) });
  const opens: any[] = h.data?.open_incidents ?? [];
  const foc = opens.find((o) => o.number === incident) ?? (incident ? { number: incident } : null);
  const withParent = opens.find((o) => o.parent_incident);
  const noParent = opens.find((o) => !o.parent_incident);
  const conn = opens.find((o) => ["store_offline", "wan_primary_down", "network_slow"].includes(o.issue_type));
  return {
    health: h.data,
    timeline: foc?.parent_incident ? foc.number : foc && !withParent ? foc.number : withParent?.number ?? opens[0]?.number,
    fixes: foc && !foc.parent_incident ? foc.number : noParent?.number ?? opens[0]?.number,
    changes: foc?.number ?? opens[0]?.number,
    blast: blastAnchor ?? (foc?.parent_incident ? foc.number : conn?.number ?? opens[0]?.number),
    major: withParent?.parent_incident ?? null,
  };
}

export function EvidenceTabs({ storeId }: { storeId: number }) {
  const { tab, setTab, pulseTab } = useApp();
  const ctx = useEvidenceContext(storeId);
  return (
    <Tabs.Root value={tab} onValueChange={setTab} className="card flex h-full min-h-[560px] flex-col overflow-hidden">
      <Tabs.List className="flex gap-1 overflow-x-auto border-b border-border px-2 pt-2 scroll-thin" aria-label="Evidence">
        {TABS.map(([v, l]) => (
          <Tabs.Trigger key={v} value={v} data-testid={`tab-${v}`}
            className={cls("relative whitespace-nowrap rounded-t-lg px-2.5 py-2 text-xs font-medium text-muted data-[state=active]:bg-surface2 data-[state=active]:text-text",
              pulseTab === v && "ring-2 ring-info/70")}>{l}</Tabs.Trigger>))}
      </Tabs.List>
      <div className="flex-1 overflow-auto p-3 scroll-thin">
        <Tabs.Content value="timeline">{ctx.timeline ? <TimelineTab num={ctx.timeline} off={ctx.health?.utc_offset_std ?? -6} /> : <Empty>No open incident to build a timeline from.</Empty>}</Tabs.Content>
        <Tabs.Content value="fixes">{ctx.fixes ? <FixesTab num={ctx.fixes} /> : <Empty>No incident selected.</Empty>}</Tabs.Content>
        <Tabs.Content value="changes"><ChangesTab num={ctx.changes} storeId={storeId} off={ctx.health?.utc_offset_std ?? -6} /></Tabs.Content>
        <Tabs.Content value="blast">{ctx.blast ? <BlastTab num={ctx.blast} /> : <Empty>No incident to anchor on.</Empty>}</Tabs.Content>
        <Tabs.Content value="impact"><ImpactTab storeId={storeId} major={ctx.major} /></Tabs.Content>
      </div>
    </Tabs.Root>
  );
}

function useRes(key: any[], url: string | null) {
  const { persona } = useApp();
  return useQuery({ queryKey: [...key, persona?.user_id], queryFn: () => api(url!), enabled: !!url });
}

// ---------------------------------------------------------------- timeline
function TimelineTab({ num, off }: { num: string; off: number }) {
  const q = useRes(["timeline", num], `/api/incidents/${num}/timeline`);
  const [open, setOpen] = useState<Record<number, boolean>>({});
  if (q.isLoading) return <Skeleton className="h-64" />;
  if (q.error) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const d = q.data, s = d.summary;
  return (
    <div className="space-y-3" data-testid="timeline">
      {s.parent && (
        <div className="rounded-lg border border-crit/30 bg-crit/5 p-3 text-xs">
          <div className="mb-1 flex items-center justify-between"><span className="font-medium">Parent <CitationChip id={s.parent} /></span>{s.parent_state && <span className="chip">{s.parent_state}</span>}</div>
          <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-muted">
            <span>First alert <b className="text-text">{localTime(s.first_alert, off)}</b></span>
            <span>Major declared <b className="text-text">{localTime(s.major_declared, off)}</b></span>
            <span>Duration <b className="text-text">{hours(s.duration_h)}{s.ongoing ? " (ongoing)" : ""}</b></span>
            <span>Children <b className="text-text">{s.child_count}</b> · {s.stores} stores</span>
            <span>Offline / degraded <b className="text-text">{s.stores_offline} / {s.stores_degraded}</b></span>
            {s.isp_ticket && <span>ISP ticket <b className="font-mono text-text">{s.isp_ticket}</b></span>}
          </div>
          <div className="mt-2"><PirButton num={s.parent} /></div>
        </div>)}
      <ol className="relative space-y-2 border-l border-border pl-4">
        {d.events.map((e: any, i: number) => (
          <li key={i} className="relative">
            <span className={cls("absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-surface",
              e.emphasis === "parent" ? "bg-crit" : e.emphasis === "self" ? "bg-info" : "bg-muted")} />
            <div className="flex items-baseline gap-2 text-[11px] text-muted"><span className="font-mono">{localTime(e.ts, off)}</span>
              {e.incident_number && <span className="font-mono">{e.incident_number}</span>}<span>{e.actor}</span></div>
            <div className={cls("text-xs leading-relaxed", e.emphasis === "parent" && "font-medium")}>
              {e.kind === "child_group" ? (
                <button className="flex items-center gap-1 text-left" onClick={() => setOpen((o) => ({ ...o, [i]: !o[i] }))}>
                  {open[i] ? <ChevronDown size={13} /> : <ChevronRight size={13} />}<span>{e.text}</span></button>) : <CitedText text={e.text} />}
            </div>
            {e.kind === "child_group" && open[i] && (
              <ul className="mt-1 max-h-48 space-y-0.5 overflow-auto rounded border border-border p-1.5 text-[11px] scroll-thin">
                {e.children.map((c: any) => <li key={c.number} className="flex gap-2"><CitationChip id={c.number} /><span className="text-muted">store {c.store_id}</span><span className="truncate">{c.short_description}</span></li>)}
              </ul>)}
          </li>))}
      </ol>
      {d.suggested_links?.length > 0 && (
        <div className="rounded-lg border border-warn/40 bg-warn/10 p-2.5 text-xs"><div className="mb-1 flex items-center gap-1 font-medium text-warn"><Link2Off size={13} /> {d.suggested_links.length} unlinked look-alikes</div>
          {d.suggested_links.slice(0, 6).map((s: any) => <div key={s.number}><CitationChip id={s.number} /> store {s.store_id} — {s.short_description}</div>)}</div>)}
    </div>
  );
}

// ---------------------------------------------------------------- fixes
function FixesTab({ num }: { num: string }) {
  const q = useRes(["similar", num], `/api/incidents/${num}/similar`);
  const [tier, setTier] = useState<"device" | "store" | "fleet">("device");
  if (q.isLoading) return <Skeleton className="h-64" />;
  if (q.error) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const d = q.data, det = d.recommendation_detail ?? {};
  const max = Math.max(0.0001, ...d.fix_table.map((r: any) => r.recurrence_30d));
  const list = d.tiers[tier] ?? [];
  return (
    <div className="space-y-3" data-testid="fixes">
      {det.device_failures_365d >= 3 && (
        <div className="rounded-lg border border-warn/40 bg-warn/10 p-3 text-sm">
          <div className="font-semibold text-warn">Failure #{det.device_failures_365d} on {d.cmdb_ci} in 12 months</div>
          <div className="mt-1 text-xs leading-relaxed"><CitedText text={d.recommendation} /></div>
        </div>)}
      {det.device_fix_table?.length > 0 && (
        <div><div className="label mb-1">Fixes applied on this device</div>
          <table className="w-full text-xs"><thead className="text-muted"><tr><th className="text-left font-normal">Fix</th><th className="font-normal">n</th><th className="font-normal">failed again ≤30d</th></tr></thead>
            <tbody>{det.device_fix_table.map((r: any) => <tr key={r.fix_label} className="border-t border-border"><td className="py-1">{r.fix_label}</td><td className="text-center tabular">{r.n}</td><td className="text-center tabular">{r.recurred} / {r.n}</td></tr>)}</tbody></table></div>)}
      <div>
        <div className="label mb-1">Which fix holds? <span className="normal-case tracking-normal">fleet · {d.issue_type} · {d.fix_table_n} resolved</span></div>
        <table className="w-full text-xs" data-testid="fix-table">
          <thead className="text-muted"><tr><th className="text-left font-normal">Fix type</th><th className="font-normal">n</th><th className="font-normal">median TTR</th><th className="w-[38%] text-left font-normal">30-day recurrence</th></tr></thead>
          <tbody>{d.fix_table.map((r: any, i: number) => (
            <tr key={r.fix_label} className="border-t border-border">
              <td className="py-1.5">{r.fix_label}{i === 0 && <span className="ml-1 chip border-accent/40 text-accent">lowest</span>}</td>
              <td className="text-center tabular">{r.n}</td><td className="text-center tabular">{hours(r.median_ttr_h)}</td>
              <td><div className="flex items-center gap-1.5"><div className="h-2 flex-1 rounded bg-surface2"><div className={cls("h-2 rounded", i === 0 ? "bg-accent" : "bg-warn")} style={{ width: `${(r.recurrence_30d / max) * 100}%` }} /></div><span className="w-10 text-right tabular">{pct(r.recurrence_30d, 1)}</span></div></td>
            </tr>))}</tbody>
        </table>
      </div>
      <div>
        <div className="mb-1 flex gap-1">{(["device", "store", "fleet"] as const).map((t) => (
          <button key={t} onClick={() => setTier(t)} className={cls("chip", tier === t && "border-info text-info")}>{t === "device" ? "This device" : t === "store" ? "This store" : "Fleet"} · {d.tiers[t].length}</button>))}</div>
        <ul className="space-y-1.5">{list.slice(0, 10).map((x: any) => (
          <li key={x.number} className="rounded-lg border border-border p-2 text-xs">
            <div className="flex items-center gap-2"><CitationChip id={x.number} /><span className="text-muted">{localTime(x.opened_at, -6, true)}</span>{x.recurred_30d && <span className="chip border-warn/40 text-warn">failed again ≤30d</span>}<span className="ml-auto chip">{x.fix_label}</span></div>
            <div className="mt-1 text-muted">{x.close_notes}</div>
          </li>))}{list.length === 0 && <Empty>None.</Empty>}</ul>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- changes
function ChangesTab({ num, storeId, off }: { num?: string; storeId: number; off: number }) {
  const q = useRes(["changes", num ?? storeId], num ? `/api/incidents/${num}/changes` : `/api/stores/${storeId}/changes`);
  if (q.isLoading) return <Skeleton className="h-64" />;
  if (q.error) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const d = q.data;
  return (
    <div className="space-y-3" data-testid="changes">
      {d.negative_evidence.map((t: string) => <div key={t} className="flex items-start gap-2 rounded-lg border border-accent/40 bg-accent/10 p-2.5 text-xs"><ShieldAlert size={14} className="mt-0.5 shrink-0 text-accent" />{t}</div>)}
      <div><div className="label mb-1">Correlated ({d.lookback_h}h lookback)</div>
        {d.candidates.length === 0 ? <Empty>No candidate changes.</Empty> : d.candidates.slice(0, 5).map((c: any) => (
          <div key={c.change.number} className="mb-2 rounded-lg border border-border p-2.5 text-xs">
            <div className="flex items-center gap-2"><CitationChip id={c.change.number} /><span className="flex-1 truncate">{c.change.short_description}</span>
              <span className={cls("chip", c.score > 0.6 ? "border-crit/50 text-crit" : c.score > 0.4 ? "border-warn/50 text-warn" : "text-muted")}>score {c.score.toFixed(2)}</span></div>
            <div className="mt-1 text-muted">{c.evidence_text}</div>
            {c.evidence?.kind === "staggered" && (
              <div className="mt-2 h-20"><ResponsiveContainer><BarChart layout="vertical" data={[{ n: "changed", v: c.evidence.treated_rate }, { n: "not yet", v: c.evidence.control_rate }]} margin={{ left: 0, right: 30 }}>
                <XAxis type="number" hide /><YAxis type="category" dataKey="n" width={60} tick={{ fontSize: 11, fill: "rgb(var(--muted))" }} axisLine={false} tickLine={false} />
                <RTooltip formatter={(v: any) => [`${v} / 100 stores / day`, "rate"]} contentStyle={{ background: "rgb(var(--surface))", border: "1px solid rgb(var(--border))", fontSize: 12 }} />
                <Bar dataKey="v" radius={3} label={{ position: "right", fontSize: 11, fill: "rgb(var(--muted))" }}>
                  <Cell fill="rgb(var(--crit))" /><Cell fill="rgb(var(--muted))" /></Bar></BarChart></ResponsiveContainer></div>)}
          </div>))}
      </div>
      <div><div className="label mb-1">Upcoming (next 7 days)</div>
        {d.upcoming.length === 0 ? <Empty>Nothing scheduled.</Empty> : d.upcoming.map((u: any) => (
          <div key={u.number} className={cls("mb-1.5 flex items-center gap-2 rounded-lg border p-2 text-xs", !u.enterprise ? "border-info/50 bg-info/10" : "border-border")}>
            <CitationChip id={u.number} /><span className="flex-1">{u.short_description}</span><span className="whitespace-nowrap text-muted">{localTime(u.planned_start, off, true)}</span></div>))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- blast
function BlastTab({ num }: { num: string }) {
  const q = useRes(["blast", num], `/api/incidents/${num}/blast-radius`);
  if (q.isLoading) return <Skeleton className="h-80" />;
  if (q.error) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const d = q.data, c = d.likely_common_cause;
  const maxv = Math.max(d.observed, d.expected, 1);
  return (
    <div className="space-y-3" data-testid="blast">
      <BlastMap data={d} />
      {c ? (
        <div className="rounded-lg border border-crit/40 bg-crit/10 p-3" data-testid="cause-card">
          <div className="label">Likely common cause</div>
          <div className="mt-0.5 text-base font-semibold">{c.value} <span className="text-sm font-normal text-muted">({c.label})</span></div>
          <div className="mt-1 text-xs"><span className="tabular font-semibold">{pct(c.support)}</span> of affected stores vs <span className="tabular">{pct(c.base)}</span> of {c.population} · lift <span className="tabular font-semibold">{c.lift.toFixed(1)}×</span></div>
          <div className="mt-1 text-[11px] text-muted">Labeled “likely”, not confirmed: attribute lift with support ≥ 60% and lift ≥ 1.5×.</div>
        </div>) : <div className="rounded-lg border border-border p-3 text-sm text-muted">{d.summary}</div>}
      <div className="grid grid-cols-3 gap-2 text-center text-xs">
        <div className="rounded-lg border border-border p-2"><div className="label">Stores</div><div className="text-lg font-semibold tabular">{d.affected_store_count}</div></div>
        <div className="rounded-lg border border-border p-2"><div className="label">Offline</div><div className="text-lg font-semibold tabular text-crit">{d.offline_count}</div></div>
        <div className="rounded-lg border border-border p-2"><div className="label">Degraded</div><div className="text-lg font-semibold tabular text-warn">{d.degraded_count}</div></div>
      </div>
      <div className="rounded-lg border border-border p-2.5 text-xs">
        <div className="mb-1 flex justify-between"><span className="label">Observed vs expected</span><span className={d.unusual ? "text-crit" : "text-muted"}>{d.unusual ? "unusual (p < 0.01)" : "within normal range"}</span></div>
        {[["observed", d.observed, "bg-crit"], ["expected (8-wk same hour)", d.expected, "bg-muted"]].map(([l, v, col]: any) => (
          <div key={l} className="mt-1 flex items-center gap-2"><span className="w-36 text-muted">{l}</span><div className="h-2 flex-1 rounded bg-surface2"><div className={cls("h-2 rounded", col)} style={{ width: `${Math.max(2, (v / maxv) * 100)}%` }} /></div><span className="w-8 text-right tabular">{Number(v).toFixed(v % 1 ? 1 : 0)}</span></div>))}
      </div>
      <div className="flex flex-wrap items-center gap-1 text-xs"><MapPin size={13} className="text-muted" />{d.markets.map((m: string) => <span key={m} className="chip">{m}</span>)}
        {d.geo && <span className="text-muted">· radius {Math.round(d.geo.max_radius_km)} km</span>}
        {d.out_of_region_store_count > 0 && <span className="text-muted">· +{d.out_of_region_store_count} outside your region</span>}</div>
      {d.parent_incidents.map((p: any) => <div key={p.number} className="text-xs">Parent <CitationChip id={p.number} /> · {p.child_count} children · {p.state}</div>)}
      {d.est_impact_usd_so_far !== null && <div className="text-xs text-muted">Est. lost sales across these stores: <span className="text-money">{money(d.est_impact_usd_so_far)}</span> <Footnote /></div>}
      {d.suggested_link_count > 0 && <div className="text-xs text-warn">{d.suggested_link_count} look-alike tickets without a parent link</div>}
    </div>
  );
}

// ---------------------------------------------------------------- impact
const STREAM_COL: Record<string, string> = { inside: "rgb(var(--info))", fuel: "rgb(var(--warn))", sevennow: "rgb(var(--accent))", lottery: "#a78bfa", ev: "#38bdf8" };
const STREAM_LAB: Record<string, string> = { inside: "Inside sales", fuel: "Fuel", sevennow: "7NOW", lottery: "Lottery", ev: "EV" };

function ImpactBlock({ title, d }: { title: string; d: any }) {
  const usd = d.usd_so_far ?? d.usd;
  if (usd === null || usd === undefined) return <div className="rounded-lg border border-border p-3 text-sm text-muted">{title}: financials restricted for your role.</div>;
  const rows = Object.entries(d.by_stream ?? {}).filter(([, v]: any) => v > 0).map(([k, v]: any) => ({ k, v, name: STREAM_LAB[k] ?? k }));
  return (
    <div className="rounded-lg border border-border p-3">
      <div className="label">{title}</div>
      <div className="mt-1 flex items-end gap-4"><div className="text-2xl font-semibold"><Ticker base={usd} perHour={d.per_hour_now} k={d.as_of} /></div>
        {d.per_hour_now > 0 && <div className="pb-1 text-xs text-muted"><span className="text-money tabular">{money(d.per_hour_now, { compact: false })}</span>/hour now</div>}</div>
      {rows.length > 0 && (
        <div className="mt-2 h-28"><ResponsiveContainer><BarChart data={rows} layout="vertical" margin={{ left: 0, right: 40 }}>
          <XAxis type="number" hide /><YAxis type="category" dataKey="name" width={80} tick={{ fontSize: 11, fill: "rgb(var(--muted))" }} axisLine={false} tickLine={false} />
          <RTooltip formatter={(v: any) => money(v, { compact: false })} contentStyle={{ background: "rgb(var(--surface))", border: "1px solid rgb(var(--border))", fontSize: 12 }} />
          <Bar dataKey="v" radius={3} label={{ position: "right", fontSize: 11, fill: "rgb(var(--muted))", formatter: (v: any) => money(v) }}>{rows.map((r) => <Cell key={r.k} fill={STREAM_COL[r.k]} />)}</Bar>
        </BarChart></ResponsiveContainer></div>)}
      {d.top_stores && <div className="mt-1 text-xs text-muted">Top stores: {d.top_stores.slice(0, 3).map((t: any) => <span key={t.store_id} className="mr-2">{t.store_id} <span className="text-money">{money(t.usd)}</span></span>)}</div>}
    </div>
  );
}

function ImpactTab({ storeId, major }: { storeId: number; major: string | null }) {
  const m = useRes(["impact-major", major], major ? `/api/incidents/${major}/impact` : null);
  const s = useRes(["impact-store", storeId], `/api/stores/${storeId}/impact?days=365`);
  return (
    <div className="space-y-3" data-testid="impact">
      {major && (m.isLoading ? <Skeleton className="h-40" /> : m.data && <ImpactBlock title={`Major ${major} — all affected stores`} d={m.data} />)}
      {s.isLoading ? <Skeleton className="h-24" /> : s.data && <ImpactBlock title={`Store ${storeId} — last 365 days`} d={{ ...s.data, by_stream: null }} />}
      <div className="flex items-start gap-1.5 text-[11px] text-muted"><FileText size={12} className="mt-0.5" />Estimate: hourly sales baseline × service loss profile, integrated per local hour. Gross sales, not margin.</div>
    </div>
  );
}
