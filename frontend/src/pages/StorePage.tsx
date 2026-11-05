import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Store } from "lucide-react";
import { useEffect } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AnswerPane } from "../components/AnswerPane";
import { EvidenceTabs } from "../components/Evidence";
import { HealthCard } from "../components/HealthCard";
import { Card, ErrorState, Ticker } from "../components/ui";
import { api } from "../lib/api";
import { useApp } from "../lib/store";

export function StorePage() {
  const { id } = useParams();
  const sid = Number(id);
  const { storeId, setStoreId, persona } = useApp();
  useEffect(() => { if (sid && sid !== storeId) setStoreId(sid); }, [sid, storeId, setStoreId]);
  const h = useQuery({ queryKey: ["health", sid, persona?.user_id], queryFn: () => api(`/api/stores/${sid}/health`), enabled: !!sid, retry: false });
  if (h.error) return <div className="p-6"><ErrorState error={h.error} /></div>;
  return (
    <div className="grid gap-4 p-4 min-[1400px]:grid-cols-12 min-[1400px]:h-[calc(100vh-108px)]" data-testid="store-view">
      <div className="min-[1400px]:col-span-4 min-[1400px]:overflow-auto scroll-thin"><HealthCard storeId={sid} /></div>
      <div className="min-h-[640px] min-[1400px]:min-h-0 min-[1400px]:col-span-5"><AnswerPane storeId={sid} /></div>
      <div className="min-h-[640px] min-[1400px]:min-h-0 min-[1400px]:col-span-3"><EvidenceTabs storeId={sid} /></div>
    </div>
  );
}

export function HomePage() {
  const { meta, setStoreId } = useApp();
  const nav = useNavigate();
  const { data: majors } = useQuery({ queryKey: ["majors"], queryFn: () => api("/api/majors?state=open"), refetchInterval: 10000 });
  const demo = meta?.demo?.store_id;
  return (
    <div className="grid gap-4 p-4 xl:grid-cols-12">
      <div className="space-y-4 xl:col-span-5">
        <Card>
          <div className="text-2xl font-semibold leading-tight">Type a store number.<br /><span className="text-accent">Get the whole story.</span></div>
          <p className="mt-2 text-sm text-muted">Profile, open incidents, whether other stores are hit, what changed, what fixed it last time, and what it costs — cited to the ticket.</p>
          {demo && <button className="btn-primary btn mt-4" data-testid="open-demo-store" onClick={() => { setStoreId(demo); nav(`/stores/${demo}`); }}><Store size={15} /> Open store {demo} <ArrowRight size={14} /></button>}
          <div className="mt-3 text-xs text-muted">Press <kbd className="rounded border border-border px-1">/</kbd> to focus the command bar.</div>
        </Card>
        <Card title="Open major incidents">
          {(majors ?? []).length === 0 ? <div className="text-sm text-muted">None.</div> : majors.map((m: any) => (
            <button key={m.number} onClick={() => { if (m.anchor_store) { setStoreId(m.anchor_store); nav(`/stores/${m.anchor_store}`); } }}
              className="flex w-full items-center gap-3 rounded-lg border border-crit/40 bg-crit/5 p-3 text-left hover:bg-crit/10">
              <div className="flex-1"><div className="font-medium">{m.short_description.replace("MAJOR: ", "")}</div>
                <div className="text-xs text-muted"><span className="font-mono">{m.number}</span> · {m.stores} stores · {m.offline} offline</div></div>
              <Ticker base={m.usd_so_far} perHour={m.per_hour_now} k={m.as_of} className="text-lg" />
            </button>))}
        </Card>
        <Card title="Scenarios in the mock data">
          <ul className="space-y-1 text-xs">{(meta?.demo?.scenarios ?? []).map((s: any) => <li key={s.id} className="flex gap-2"><span className="chip font-mono">{s.id}</span><span className="flex-1">{s.title}</span><span className="text-muted">{s.status}</span></li>)}</ul>
          <div className="mt-2 text-[11px] text-muted">Mock data only (hackathon rule): synthetic tickets, RFC1918 mock IPs.</div>
        </Card>
      </div>
      <div className="min-h-[640px] xl:col-span-7"><AnswerPane storeId={null} /></div>
    </div>
  );
}

export function StoresIndex() {
  const { setStoreId, meta } = useApp();
  const nav = useNavigate();
  const { data } = useQuery({ queryKey: ["fleet-overview"], queryFn: () => api("/api/fleet/overview") });
  const { data: chronic } = useQuery({ queryKey: ["chronic"], queryFn: () => api("/api/fleet/chronic") });
  return (
    <div className="space-y-4 p-4">
      <Card title="Stores"><div className="text-sm text-muted">Use the command bar to search by store number, city or market. Demo store: <Link className="text-info underline" to={`/stores/${meta?.demo?.store_id}`} onClick={() => setStoreId(meta?.demo?.store_id)}>{meta?.demo?.store_id}</Link>.</div></Card>
      <Card title="Stores with chronic devices">
        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">{(chronic?.items ?? []).slice(0, 12).map((c: any) => (
          <button key={c.unit} onClick={() => { setStoreId(c.store_id); nav(`/stores/${c.store_id}`); }} className="rounded-lg border border-border p-2 text-left text-xs hover:border-info/60">
            <div className="font-mono font-medium">{c.store_id}</div><div className="text-muted">{c.city}, {c.state} · {c.unit} · {c.count_365d} failures</div></button>))}</div>
      </Card>
      {data && <div className="text-xs text-muted">{data.total_open} open store incidents fleet-wide.</div>}
    </div>
  );
}
