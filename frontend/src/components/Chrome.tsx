import { useQuery } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { Activity, BarChart3, ChevronDown, ClipboardList, Moon, Search, Settings, ShieldCheck, Store, Sun, X, Gauge, MessageSquare } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { cls, money } from "../lib/format";
import { useApp, type Persona } from "../lib/store";
import { Ticker } from "./ui";

export function Logo() {
  return (
    <div className="flex items-center gap-2 font-semibold tracking-tight" aria-label="StoreLens">
      <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden><circle cx="13" cy="13" r="9" fill="none" stroke="rgb(var(--accent))" strokeWidth="3.2" /><circle cx="13" cy="13" r="3.4" fill="rgb(var(--accent))" /><path d="M20 20l8 8" stroke="rgb(var(--accent))" strokeWidth="3.6" strokeLinecap="round" /></svg>
      <span className="text-[17px]">Store<span className="text-accent">Lens</span></span>
    </div>
  );
}

const ROLE_BADGE: Record<string, string> = { exec: "bg-money/15 text-money border-money/40", manager: "bg-info/15 text-info border-info/40", engineer: "bg-accent/15 text-accent border-accent/40" };
const initials = (n: string) => n.split(" ").map((x) => x[0]).join("").slice(0, 2);

export function PersonaSwitcher() {
  const { personas, persona, setPersona, meta } = useApp();
  const [open, setOpen] = useState(false);
  const dp = meta?.default_personas ?? {};
  const main = [dp.engineer, dp.exec, dp.manager].map((id: string) => personas.find((p) => p.user_id === id)).filter(Boolean) as Persona[];
  const others = personas.filter((p) => !main.includes(p));
  if (!persona) return null;
  const Item = ({ p }: { p: Persona }) => (
    <button data-testid={`persona-${p.persona}-${p.user_id}`} onClick={() => { setPersona(p.user_id); setOpen(false); }}
      className={cls("flex w-full items-center gap-3 rounded-lg px-2 py-2 text-left hover:bg-surface2", p.user_id === persona.user_id && "bg-surface2")}>
      <span className="flex h-8 w-8 items-center justify-center rounded-full bg-surface2 text-xs font-semibold">{initials(p.name)}</span>
      <span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium">{p.name}</span><span className="block truncate text-xs text-muted">{p.title}</span></span>
      <span className={cls("chip border", ROLE_BADGE[p.persona])}>{p.persona === "exec" ? "Exec" : p.persona === "manager" ? `Mgr · ${p.region_scope}` : "Engineer"}</span>
    </button>
  );
  return (
    <div className="relative">
      <button data-testid="persona-switcher" onClick={() => setOpen((o) => !o)} className="flex items-center gap-2 rounded-lg border border-border bg-surface px-2 py-1.5 hover:border-info/60" aria-haspopup="menu" aria-expanded={open}>
        <span className="flex h-7 w-7 items-center justify-center rounded-full bg-surface2 text-xs font-semibold">{initials(persona.name)}</span>
        <span className="hidden text-left leading-tight lg:block"><span className="block text-sm font-medium">{persona.name}</span><span className="block text-[11px] text-muted">{persona.title}</span></span>
        <span className={cls("chip border", ROLE_BADGE[persona.persona])}>{persona.persona === "exec" ? "Exec" : persona.persona === "manager" ? "Ops Mgr" : "Engineer"}</span>
        <ChevronDown size={14} className="text-muted" />
      </button>
      <AnimatePresence>{open && (
        <motion.div initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.15 }}
          className="absolute right-0 z-50 mt-2 w-96 rounded-xl border border-border bg-surface p-2 shadow-2xl" role="menu">
          <div className="label px-2 py-1">Demo personas</div>
          {main.map((p) => <Item key={p.user_id} p={p} />)}
          <div className="label px-2 pb-1 pt-3">More</div>
          <div className="max-h-56 overflow-auto scroll-thin">{others.map((p) => <Item key={p.user_id} p={p} />)}</div>
          <div className="mt-2 border-t border-border px-2 pt-2 text-[11px] text-muted">RBAC is enforced in the data layer: network details and $ are masked before the model or UI sees them.</div>
        </motion.div>)}
      </AnimatePresence>
    </div>
  );
}

export function CommandBar() {
  const { ask, setStoreId, setIncident, storeId } = useApp();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [focus, setFocus] = useState(false);
  const ref = useRef<HTMLInputElement>(null);
  const isNum = /^\d{1,5}$/.test(q.trim());
  const { data: sugg } = useQuery({ queryKey: ["search", q], queryFn: () => api(`/api/stores/search?q=${encodeURIComponent(q.trim())}`), enabled: focus && (isNum || q.trim().length >= 3) && !/\?|\s.*\s/.test(q) });
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && tag !== "INPUT" && tag !== "TEXTAREA")) { e.preventDefault(); ref.current?.focus(); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);
  const go = (raw: string) => {
    const v = raw.trim();
    if (!v) return;
    setQ(""); ref.current?.blur();
    if (/^\d{5}$/.test(v)) { setStoreId(Number(v)); setIncident(null); nav(`/stores/${v}`); return; }
    const inc = v.match(/^INC\d{7}$/i);
    if (inc) {
      api(`/api/incidents/${inc[0].toUpperCase()}`).then((r: any) => {
        if (r.store_id) { setStoreId(r.store_id); nav(`/stores/${r.store_id}`); }
        setIncident(inc[0].toUpperCase());
        ask(`What's going on with ${inc[0].toUpperCase()}?`, { storeId: r.store_id ?? null, incidentNumber: inc[0].toUpperCase() });
      }).catch(() => {});
      return;
    }
    const sid = v.match(/\b(\d{5})\b/);
    const target = sid ? Number(sid[1]) : storeId;
    if (sid) { setStoreId(target); nav(`/stores/${target}`); }
    else if (storeId) nav(`/stores/${storeId}`); else nav("/");
    ask(v, { storeId: target ?? null, incidentNumber: null });
  };
  return (
    <div className="relative w-full max-w-2xl">
      <div className={cls("flex items-center gap-2 rounded-xl border bg-surface px-3 py-2 transition-colors", focus ? "border-info" : "border-border")}>
        <Search size={16} className="text-muted" aria-hidden />
        <input ref={ref} data-testid="command-bar" value={q} onChange={(e) => setQ(e.target.value)} onFocus={() => setFocus(true)} onBlur={() => setTimeout(() => setFocus(false), 150)}
          onKeyDown={(e) => { if (e.key === "Enter") go(q); if (e.key === "Escape") { setQ(""); ref.current?.blur(); } }}
          placeholder="Type a store number, an INC number, or ask a question…" aria-label="Command bar"
          className="w-full bg-transparent text-[15px] outline-none placeholder:text-muted" />
        <kbd className="hidden rounded border border-border px-1.5 text-[11px] text-muted md:block">⌘K</kbd>
      </div>
      {focus && sugg && sugg.length > 0 && (
        <div className="absolute z-50 mt-1 w-full overflow-hidden rounded-xl border border-border bg-surface shadow-2xl">
          {sugg.map((s: any) => (
            <button key={s.store_id} onMouseDown={() => go(String(s.store_id))} className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-surface2">
              <Store size={15} className="text-muted" /><span className="font-mono">{s.store_id}</span><span className="text-sm">{s.city}, {s.state}</span>
              <span className="ml-auto text-xs text-muted">{s.store_format} · {s.region}</span>
            </button>))}
        </div>)}
    </div>
  );
}

export function Clock() {
  const { meta } = useApp();
  const [, force] = useState(0);
  useEffect(() => { const i = setInterval(() => force((x) => x + 1), 15000); return () => clearInterval(i); }, []);
  return <div className="hidden whitespace-nowrap text-xs text-muted xl:block" data-testid="clock">{meta?.now_display ?? ""}</div>;
}

export function SettingsMenu() {
  const { theme, toggleTheme, meta, setDrawer } = useApp();
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button className="btn px-2" onClick={() => setOpen((o) => !o)} aria-label="Settings"><Settings size={16} /></button>
      {open && (
        <div className="absolute right-0 z-50 mt-2 w-72 rounded-xl border border-border bg-surface p-3 text-sm shadow-2xl">
          <button className="btn w-full justify-between" onClick={toggleTheme}>{theme === "dark" ? <><span>Light theme</span><Sun size={14} /></> : <><span>Dark theme</span><Moon size={14} /></>}</button>
          <div className="mt-3 space-y-1 text-xs text-muted">
            <div>Provider chain: <span className="font-mono text-text">{(meta?.provider?.chain ?? []).join(" → ")}</span></div>
            <div>Live LLM available: <span className="text-text">{(meta?.provider?.live_available ?? []).join(", ") || "none (replay/template)"}</span></div>
            <div>Clock: <span className="text-text">{meta?.clock_mode}</span></div>
          </div>
          <button className="btn mt-3 w-full" onClick={() => { setDrawer({ kind: "trace" }); setOpen(false); }}><Activity size={14} /> Open trace drawer</button>
          <div className="mt-2 text-[11px] text-muted">Shortcuts: ⌘K or / focus · Esc close · Shift+R reset demo</div>
        </div>)}
    </div>
  );
}

export function TopBar() {
  return (
    <header className="sticky top-0 z-40 flex h-16 items-center gap-4 border-b border-border bg-bg/90 px-4 backdrop-blur">
      <NavLink to="/" className="shrink-0"><Logo /></NavLink>
      <div className="flex flex-1 justify-center"><CommandBar /></div>
      <Clock />
      <PersonaSwitcher />
      <SettingsMenu />
    </header>
  );
}

export function MajorBanner() {
  const { bannerOpen, setBannerOpen, setStoreId, setTab, setBlastAnchor, setIncident } = useApp();
  const nav = useNavigate();
  const { data } = useQuery({ queryKey: ["majors"], queryFn: () => api("/api/majors?state=open"), refetchInterval: 10000 });
  const m = data?.[0];
  if (!bannerOpen || !m) return null;
  const title = m.short_description.replace(/^MAJOR:\s*/, "").split(" - ")[0];
  const mk = (m.markets ?? []).includes("Dallas") ? "DFW" : (m.markets ?? []).slice(0, 2).join("/");
  return (
    <div data-testid="major-banner" className="flex items-center gap-3 border-b border-crit/40 bg-crit/15 px-4 py-2 text-sm">
      <span className="relative flex h-2.5 w-2.5"><span className="pulse-ring absolute inline-flex h-full w-full rounded-full bg-crit" /><span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-crit" /></span>
      <button className="flex flex-1 items-center gap-2 text-left" onClick={() => { if (m.anchor_store) { setStoreId(m.anchor_store); nav(`/stores/${m.anchor_store}`); } setIncident(null); setBlastAnchor(m.number); setTab("blast"); }}>
        <span className="font-bold tracking-wide text-crit">MAJOR</span><span className="text-muted">·</span>
        <span className="font-medium">{title} — {mk}</span><span className="text-muted">·</span>
        <span className="tabular">{m.stores} stores</span><span className="text-muted">·</span>
        <span className="tabular">{m.offline} cash-only</span><span className="text-muted">·</span>
        {m.usd_so_far === null ? <span className="text-muted">$ restricted</span> :
          <span><Ticker base={m.usd_so_far} perHour={m.per_hour_now} k={m.as_of} /> <span className="text-muted">est. lost sales and counting</span></span>}
        <span className="ml-1 font-mono text-xs text-muted">{m.number} ▸</span>
      </button>
      <button onClick={() => setBannerOpen(false)} aria-label="Dismiss banner" className="rounded p-1 text-muted hover:bg-surface2"><X size={15} /></button>
    </div>
  );
}

const NAV = [
  { to: "/", label: "Ask", icon: MessageSquare, end: true },
  { to: "/stores", label: "Stores", icon: Store },
  { to: "/ops", label: "Operations", icon: ClipboardList },
  { to: "/fleet", label: "Fleet insights", icon: BarChart3 },
  { to: "/impact", label: "Impact", icon: Gauge },
  { to: "/audit", label: "Audit", icon: ShieldCheck },
];

export function LeftNav() {
  const { storeId } = useApp();
  return (
    <nav className="flex w-[76px] shrink-0 flex-col items-center gap-1 border-r border-border bg-bg py-3" aria-label="Main">
      {NAV.map(({ to, label, icon: I, end }) => (
        <NavLink key={to} to={to === "/stores" && storeId ? `/stores/${storeId}` : to} end={end}
          className={({ isActive }) => cls("flex w-16 flex-col items-center gap-1 rounded-lg py-2 text-[11px] text-muted hover:bg-surface2 hover:text-text", isActive && "bg-surface2 text-text")}>
          <I size={18} aria-hidden />{label}
        </NavLink>))}
    </nav>
  );
}

export { money };
