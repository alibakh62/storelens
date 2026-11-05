import { useQuery } from "@tanstack/react-query";
import { Timer } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Card, ErrorState, Skeleton } from "../components/ui";
import { api } from "../lib/api";
import { cls, localTime, money } from "../lib/format";

// ---------------------------------------------------------------- Impact / value model with sliders
const SLIDERS: [string, string, number, number, number, string?][] = [
  ["fleet_store_count", "Fleet store count", 1000, 20000, 500],
  ["ops_users", "Ops users", 20, 1000, 10],
  ["lookups_per_user_per_day", "Lookups per user per day", 1, 40, 1],
  ["loaded_cost_per_hour", "Loaded $/hour", 20, 120, 1],
  ["sales_recapture", "Sales recapture share", 0, 0.9, 0.05],
];

export function ImpactPage() {
  const [ov, setOv] = useState<Record<string, number>>({});
  const [adopt, setAdopt] = useState(1);
  const qs = useMemo(() => {
    const p = new URLSearchParams();
    Object.entries(ov).forEach(([k, v]) => p.set(k, JSON.stringify(v)));
    if (adopt !== 1) p.set("ops_users", JSON.stringify(Math.round((ov.ops_users ?? 300) * adopt)));
    return p.toString();
  }, [ov, adopt]);
  const q = useQuery({ queryKey: ["value", qs], queryFn: () => api(`/api/value-model?${qs}`), placeholderData: (p) => p });
  const d = q.data;
  const label = (l: string) => <span className={cls("chip", l === "measured" ? "border-accent/50 text-accent" : l === "mock-derived" ? "border-info/50 text-info" : "border-warn/50 text-warn")}>{l}</span>;
  return (
    <div className="grid gap-4 p-4 xl:grid-cols-12">
      <div className="space-y-4 xl:col-span-8">
        <Card title="Value model — low / base / high (annual)">
          {!d ? <Skeleton className="h-48" /> : (<>
            <table className="w-full text-sm" data-testid="value-table"><thead className="text-xs text-muted"><tr><th className="text-left font-normal">Lever</th><th className="text-right font-normal">Low</th><th className="text-right font-normal">Base</th><th className="text-right font-normal">High</th></tr></thead>
              <tbody>{d.rows.map((r: any) => (
                <tr key={r.lever} className="border-t border-border align-top"><td className="py-2"><div className="font-medium">{r.lever}</div><div className="font-mono text-[11px] text-muted">{r.formula}</div></td>
                  <td className="text-right tabular text-money">{money(r.low)}</td><td className="text-right text-lg font-semibold tabular text-money">{money(r.base)}</td><td className="text-right tabular text-money">{money(r.high)}</td></tr>))}
                <tr className="border-t-2 border-border"><td className="py-2 font-semibold">Total</td><td className="text-right tabular text-money">{money(d.total.low)}</td><td className="text-right text-xl font-semibold tabular text-money">{money(d.total.base)}</td><td className="text-right tabular text-money">{money(d.total.high)}</td></tr>
              </tbody></table>
            <div className="mt-2 text-xs text-muted">{d.note} LLM run cost ≈ <span className="text-money">{money(d.llm_monthly_cost, { compact: false })}</span>/month for {d.llm_queries_per_month.toLocaleString()} answers (tokens × price, from config).</div>
          </>)}
        </Card>
        <Card title="Assumptions">
          {d && <table className="w-full text-xs"><tbody>{d.assumptions.map((a: any) => (
            <tr key={a.key} className="border-t border-border"><td className="py-1 font-mono">{a.key}</td><td className="tabular">{typeof a.value === "object" ? `${a.value.low} / ${a.value.base} / ${a.value.high}` : a.value}</td><td>{label(a.label)}</td><td className="text-muted">{a.note ?? ""}</td></tr>))}</tbody></table>}
        </Card>
      </div>
      <div className="space-y-4 xl:col-span-4">
        <Card title="What if…">
          <label className="mb-3 block text-sm">Adoption: <b className="tabular">{Math.round(adopt * 100)}%</b> of ops users
            <input type="range" min={0.1} max={1} step={0.05} value={adopt} onChange={(e) => setAdopt(Number(e.target.value))} className="w-full accent-[rgb(var(--info))]" /></label>
          {SLIDERS.map(([k, l, mn, mx, st]) => {
            const base = d?.assumptions.find((a: any) => a.key === k)?.value;
            const v = ov[k] ?? (typeof base === "number" ? base : mn);
            return <label key={k} className="mb-3 block text-sm">{l}: <b className="tabular">{v}</b>
              <input type="range" min={mn} max={mx} step={st} value={v} onChange={(e) => setOv((o) => ({ ...o, [k]: Number(e.target.value) }))} className="w-full" /></label>;
          })}
          <button className="btn text-xs" onClick={() => { setOv({}); setAdopt(1); }}>Reset</button>
        </Card>
        <Card title="What we'll measure in the pilot"><ul className="list-disc space-y-1 pl-4 text-sm">{(d?.pilot_kpis ?? []).map((k: string) => <li key={k}>{k}</li>)}</ul></Card>
        {d && <Card title="Mock-derived inputs"><div className="space-y-1 text-xs text-muted">
          <div>Lost sales / store / year: <span className="text-money">{money(d.mock.lost_sales_per_store_per_year, { compact: false })}</span></div>
          <div>Repeat incidents (same device ≤ 30d): {(d.mock.repeat_share * 100).toFixed(1)}%</div>
          <div>Minutes to detect a cluster: {d.mock.median_minutes_to_flag} (blast-radius flag) vs {d.mock.median_minutes_to_declare} (major declared)</div>
        </div></Card>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Audit
export function AuditPage() {
  const q = useQuery({ queryKey: ["audit"], queryFn: () => api("/api/audit?limit=200"), refetchInterval: 10000, retry: false });
  return (
    <div className="p-4">
      <Card title="Audit log (append-only)">
        {q.isLoading ? <Skeleton className="h-64" /> : q.error ? <ErrorState error={q.error} /> : (
          <div className="max-h-[75vh] overflow-auto scroll-thin">
            <table className="w-full text-xs" data-testid="audit-table"><thead className="sticky top-0 bg-surface text-muted"><tr>{["time", "persona", "question / route", "tools", "mode", "validation", "latency"].map((h) => <th key={h} className="py-1 text-left font-normal">{h}</th>)}</tr></thead>
              <tbody>{q.data.map((r: any, i: number) => (
                <tr key={i} className={cls("border-t border-border align-top", r.rbac_p1 && "bg-crit/10")}>
                  <td className="whitespace-nowrap py-1 font-mono">{r.ts?.slice(11, 19)}</td><td>{r.persona_name ?? r.persona}</td>
                  <td className="max-w-md">{r.question ?? <span className="font-mono text-muted">{r.route} {JSON.stringify(r.args)}</span>}</td>
                  <td className="font-mono text-muted">{(r.tools ?? []).map((t: any) => t.name).join(", ")}</td><td>{r.provider_mode}</td>
                  <td className="text-muted">{r.validation ? `removed ${r.validation.removed_bullets ?? 0} · redactions ${r.validation.rbac_redactions ?? 0}` : `masked ${r.masked_fields ?? 0}`}</td>
                  <td className="tabular">{Math.round(r.latency_ms ?? r.ms ?? 0)} ms</td></tr>))}</tbody></table></div>)}
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------- "Old way" (X1): generic ticket list + stopwatch
export function BeforePage() {
  const [sp] = useSearchParams();
  const store = Number(sp.get("store") || 34512);
  const q = useQuery({ queryKey: ["before", store], queryFn: () => api(`/api/stores/${store}/incidents?limit=50`) });
  const [sel, setSel] = useState<string | null>(null);
  const [t0] = useState(Date.now());
  const [, tick] = useState(0);
  useEffect(() => { const i = setInterval(() => tick((x) => x + 1), 1000); return () => clearInterval(i); }, []);
  const s = Math.floor((Date.now() - t0) / 1000);
  const det = useQuery({ queryKey: ["before-inc", sel], queryFn: () => api(`/api/incidents/${sel}`), enabled: !!sel });
  return (
    <div className="min-h-full bg-[#f3f3f3] p-4 font-[Arial] text-[13px] text-black" data-testid="before">
      <div className="mb-2 flex items-center gap-3 border border-[#bbb] bg-[#e4e4e4] px-2 py-1"><b>Incidents</b><span>Location = {store}</span>
        <span className="ml-auto flex items-center gap-1 rounded bg-black px-2 py-0.5 font-mono text-white"><Timer size={13} /> {String(Math.floor(s / 60)).padStart(2, "0")}:{String(s % 60).padStart(2, "0")}</span></div>
      {q.isLoading ? "Loading…" : (
        <table className="w-full border border-[#bbb] bg-white"><thead className="bg-[#ddd]"><tr>{["Number", "Opened", "Short description", "Priority", "State", "Assignment group", "Parent"].map((h) => <th key={h} className="border border-[#ccc] px-1 text-left">{h}</th>)}</tr></thead>
          <tbody>{q.data.items.map((r: any) => (
            <tr key={r.number} className="cursor-pointer hover:bg-[#eef]" onClick={() => setSel(r.number)}>
              <td className="border border-[#ddd] px-1 text-[#0645ad] underline">{r.number}</td><td className="border border-[#ddd] px-1">{r.opened_at?.replace("T", " ").slice(0, 19)}</td>
              <td className="border border-[#ddd] px-1">{r.short_description}</td><td className="border border-[#ddd] px-1">{r.priority}</td><td className="border border-[#ddd] px-1">{r.state}</td>
              <td className="border border-[#ddd] px-1">{r.assignment_group}</td><td className="border border-[#ddd] px-1">{r.parent_incident ?? ""}</td></tr>))}</tbody></table>)}
      {sel && det.data && (
        <div className="mt-3 border border-[#bbb] bg-white p-2">
          <b>{sel}</b> <button className="ml-2 underline" onClick={() => setSel(null)}>back</button>
          <div className="mt-1 grid grid-cols-4 gap-x-2 text-[12px]">{Object.entries(det.data).filter(([k]) => k !== "work_notes" && k !== "citations").map(([k, v]) => <div key={k} className="truncate"><span className="text-[#555]">{k}:</span> {String(v ?? "")}</div>)}</div>
          <div className="mt-2 text-[12px]">{(det.data.work_notes ?? []).map((n: any, i: number) => <div key={i} className="border-t border-[#eee] py-0.5">{n.ts} {n.author}: {n.text}</div>)}</div>
        </div>)}
    </div>
  );
}

export function NotFound() {
  return <div className="p-6 text-muted">Page not found. {localTime(new Date().toISOString())}</div>;
}
