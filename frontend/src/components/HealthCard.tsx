import { useQuery } from "@tanstack/react-query";
import { animate, motion } from "framer-motion";
import { AlertTriangle, CalendarClock, ExternalLink, Fuel, Link2, Lock, Network } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { cls, hours, localTime, shortDate } from "../lib/format";
import { useApp } from "../lib/store";
import { Card, ErrorState, PriorityPill, Skeleton, STATUS, StatusBadge, Ticker } from "./ui";

const BAND: Record<string, keyof typeof STATUS> = { Healthy: "healthy", Watch: "watch", Critical: "critical" };

function Dial({ score, band, breakdown }: { score: number; band: string; breakdown: any[] }) {
  const [v, setV] = useState(0);
  useEffect(() => {
    const c = animate(0, score, { duration: 0.9, ease: "easeOut", onUpdate: (x) => setV(Math.round(x)) });
    return () => c.stop();
  }, [score]);
  const st = BAND[band] ?? "info";
  const color = st === "critical" ? "rgb(var(--crit))" : st === "watch" ? "rgb(var(--warn))" : "rgb(var(--accent))";
  const R = 46, C = 2 * Math.PI * R, arc = 0.75;
  const tip = ["Health score breakdown (start at 100):", ...breakdown.map((b) => `${b.points} · ${b.reason}`)].join("\n");
  return (
    <div className="relative h-[120px] w-[120px] shrink-0" title={tip} data-testid="health-dial">
      <svg viewBox="0 0 120 120" className="h-full w-full -rotate-[225deg]">
        <circle cx="60" cy="60" r={R} fill="none" stroke="rgb(var(--surface-2))" strokeWidth="10" strokeDasharray={`${C * arc} ${C}`} strokeLinecap="round" />
        <motion.circle cx="60" cy="60" r={R} fill="none" stroke={color} strokeWidth="10" strokeLinecap="round"
          initial={{ strokeDasharray: `0 ${C}` }} animate={{ strokeDasharray: `${(C * arc * score) / 100} ${C}` }} transition={{ duration: 0.9, ease: "easeOut" }} />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center pt-1">
        <div className="text-[34px] font-semibold leading-none tabular">{v}</div>
        <div className="mt-1"><StatusBadge status={st} label={band} /></div>
      </div>
    </div>
  );
}

export function HealthCard({ storeId }: { storeId: number }) {
  const { incident, setIncident, setDrawer, persona } = useApp();
  const h = useQuery({ queryKey: ["health", storeId, persona?.user_id], queryFn: () => api(`/api/stores/${storeId}/health`), refetchInterval: 30000 });
  const p = useQuery({ queryKey: ["profile", storeId, persona?.user_id], queryFn: () => api(`/api/stores/${storeId}/profile`) });
  if (h.isLoading) return <Card><Skeleton className="h-32" /><Skeleton className="mt-3 h-40" /><Skeleton className="mt-3 h-24" /></Card>;
  if (h.error) return <Card><ErrorState error={h.error} retry={() => h.refetch()} /></Card>;
  const d = h.data, prof = p.data, net = prof?.network ?? {};
  const off = d.utc_offset_std;
  const masked = (x: any) => x === "•••• (restricted)";
  return (
    <div className="space-y-3" data-testid="health-card">
      <Card>
        <div className="flex items-start gap-4">
          <Dial score={d.health_score} band={d.band} breakdown={d.score_breakdown} />
          <div className="min-w-0 flex-1">
            <div className="text-xs text-muted">{d.banner}</div>
            <h2 className="text-xl font-semibold leading-tight">Store <span className="font-mono" data-testid="store-number">{d.store_id}</span></h2>
            <div className="text-sm text-muted">{prof?.store?.address_line1 ? `${prof.store.address_line1}, ` : ""}{d.city}, {d.state}</div>
            <div className="mt-2 flex flex-wrap gap-1">
              <span className="chip">{d.store_format}</span><span className="chip">{d.region} · {d.market}</span>
              {prof?.store?.is_24_hours && <span className="chip">24/7</span>}
            </div>
            <div className="mt-2 text-[11px] text-muted">Local time · {d.timezone}</div>
          </div>
        </div>
        {prof && (
          <div className="mt-3 flex flex-wrap gap-1" aria-label="Services">
            {prof.services.map((s: any) => <span key={s.key} className="chip">{s.key === "has_fuel_station" && <Fuel size={12} />}{s.label}{s.detail ? ` · ${s.detail}` : ""}</span>)}
          </div>)}
        {prof && (
          <div className="mt-3 flex items-center gap-2 rounded-lg border border-border bg-surface2/50 px-2.5 py-2 text-xs">
            <Network size={14} className="text-muted" />
            <span>{net.primary_isp} <span className="text-muted">({net.circuit_type})</span> · backup {net.backup_carrier}</span>
            <span className="ml-auto font-mono text-muted">{masked(net.router_lan_ip) ? <span className="inline-flex items-center gap-1"><Lock size={11} /> IPs restricted</span> : `${net.primary_circuit_id} · ${net.router_lan_ip}`}</span>
          </div>)}
      </Card>

      <Card title={`Open incidents (${d.open_incidents.length})`} right={d.open_incidents.length ? <span className="text-xs text-muted">click to focus</span> : null}>
        {d.open_incidents.length === 0 ? <div className="text-sm text-muted">No open incidents.</div> : (
          <ul className="space-y-2">
            {d.open_incidents.map((o: any) => {
              const sel = incident === o.number;
              const sla = o.sla.status;
              return (
                <li key={o.number}>
                  <div role="button" tabIndex={0} data-testid={`open-${o.number}`} onClick={() => setIncident(sel ? null : o.number)}
                    onKeyDown={(e) => { if (e.key === "Enter") setIncident(sel ? null : o.number); }}
                    className={cls("w-full cursor-pointer rounded-lg border p-2.5 text-left transition-colors", sel ? "border-info bg-info/10" : "border-border bg-surface2/40 hover:border-info/50")}>
                    <div className="flex items-start gap-2">
                      <PriorityPill p={o.priority} />
                      <div className="min-w-0 flex-1">
                        <div className="text-sm font-medium leading-snug">{o.short_description}</div>
                        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
                          <span className="font-mono">{o.number}</span><span>·</span><span>{o.state}{o.on_hold_reason ? ` (${o.on_hold_reason})` : ""}</span><span>·</span><span>{hours(o.age_h)} old</span>
                        </div>
                        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                          <span className={cls("chip", sla === "breached" ? "border-crit/50 text-crit" : sla === "at_risk" ? "border-warn/50 text-warn" : "text-muted")}>
                            {sla === "breached" ? "SLA breached" : sla === "at_risk" ? "SLA at risk" : "SLA ok"} · due {localTime(o.sla.due, off)}</span>
                          {o.parent_incident && <span className="chip border-crit/40 text-crit"><Link2 size={11} /> part of major {o.parent_incident}</span>}
                          {o.est_impact_usd !== null && o.est_impact_usd > 0 && <span className="chip"><Ticker base={o.est_impact_usd} perHour={o.per_hour_now_usd} k={d.as_of} /></span>}
                        </div>
                      </div>
                      <button onClick={(e) => { e.stopPropagation(); setDrawer({ kind: "record", id: o.number }); }} className="rounded p-1 text-muted hover:bg-surface2" aria-label={`Open ${o.number}`}><ExternalLink size={13} /></button>
                    </div>
                  </div>
                </li>);
            })}
          </ul>)}
      </Card>

      {d.chronic_flags.map((f: any) => (
        <div key={f.unit} data-testid="chronic-alert" className="rounded-card border border-warn/40 bg-warn/10 p-3 text-sm">
          <div className="flex items-center gap-2 font-medium text-warn"><AlertTriangle size={15} /> Chronic device</div>
          <div className="mt-1"><span className="font-semibold">{friendly(f.unit)}</span> — {f.count_365d} failures in 12 months
            <span className="text-muted"> ({f.count_90d} in 90 days)</span></div>
          <div className="mt-1 text-xs text-muted">{f.recommendation}{f.problem ? ` · ${f.problem}` : ""}</div>
        </div>))}

      <Card>
        <div className="grid grid-cols-2 gap-2">
          <div className="rounded-lg border border-border bg-surface2/60 p-2.5">
            <div className="label">90-day incidents</div>
            <div className="mt-0.5 text-lg font-semibold tabular">{d.counts_90d}</div>
            <div className="text-xs text-muted">{ordinal(d.fleet_percentile_90d)} percentile of {d.store_format}</div>
          </div>
          <div className="rounded-lg border border-border bg-surface2/60 p-2.5">
            <div className="label">MTTR (90d, median)</div>
            <div className="mt-0.5 text-lg font-semibold tabular">{hours(d.mttr_90d_hours)}</div>
            <div className="text-xs text-muted">fleet {hours(d.fleet_mttr_90d_hours)}</div>
          </div>
        </div>
        <div className="mt-2 flex flex-wrap gap-1 text-xs text-muted">Top categories (90d): {d.top_categories_90d.map((c: any) => <span key={c.category} className="chip">{c.category} · {c.count}</span>)}</div>
        {d.upcoming_changes.filter((c: any) => !c.enterprise).slice(0, 2).map((c: any) => (
          <button key={c.number} onClick={() => setDrawer({ kind: "record", id: c.number })} data-testid="upcoming-change"
            className="mt-2 flex w-full items-center gap-2 rounded-lg border border-info/40 bg-info/10 px-2.5 py-2 text-left text-xs">
            <CalendarClock size={14} className="text-info" /><span className="flex-1">{c.short_description.split(": ").slice(-1)[0]}</span>
            <span className="font-mono text-muted">{shortDate(c.planned_start, off)} · {c.number}</span>
          </button>))}
      </Card>
    </div>
  );
}

export function friendly(ci?: string | null) {
  if (!ci) return "device";
  const m = ci.match(/S\d{5}-([A-Z0-9]+)-(\d{2})/);
  if (!m) return ci;
  const n = parseInt(m[2]);
  const k: Record<string, string> = { DISP: `Pump ${n} card reader`, POS: `Register ${n}`, PIN: `PIN pad ${n}`, RTR: "Router", CLR: "Walk-in cooler", "7NOW": "7NOW tablet", EV: `EV charger ${n}` };
  return k[m[1]] ?? ci;
}
function ordinal(p: number) {
  const n = Math.round(p);
  const s = n % 10 === 1 && n !== 11 ? "st" : n % 10 === 2 && n !== 12 ? "nd" : n % 10 === 3 && n !== 13 ? "rd" : "th";
  return `${n}${s}`;
}
