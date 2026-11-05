import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, chatStream, setPersonaId } from "./api";

export type Persona = {
  user_id: string; name: string; role: string; title: string; region_scope: string;
  can_view_network: boolean; can_view_financials: boolean; persona: "exec" | "manager" | "engineer";
};
export type Bullet = { text: string; citations: string[]; sources?: string[] };
export type Answer = {
  headline: string; status: "critical" | "watch" | "healthy" | "info"; bullets: Bullet[];
  sections: { title: string; bullets: Bullet[] }[];
  impact?: { usd_so_far: number | null; per_hour_now: number | null; as_of?: string; restricted?: boolean; scope?: string } | null;
  recommended_actions: { text: string; owner?: string; citations: string[] }[];
  followups: string[]; confidence: "high" | "medium" | "low"; panels: string[];
};
export type ToolChip = { name: string; label: string; done: boolean; ms?: number; rows?: number; masked_fields?: number; args?: any };
export type Turn = {
  id: string; question: string; storeId?: number | null; incidentNumber?: string | null; persona: string;
  partial: { headline: string; bullets: Bullet[] }; answer?: Answer; validation?: any; mode?: string; trace?: any;
  tools: ToolChip[]; status: "running" | "done" | "error"; error?: string; startedAt: number;
};
export type Drawer = { kind: "record"; id: string } | { kind: "trace"; turnId?: string } | null;

type Ctx = {
  meta: any; personas: Persona[]; persona: Persona | null; setPersona: (id: string) => void;
  theme: "dark" | "light"; toggleTheme: () => void;
  storeId: number | null; setStoreId: (s: number | null) => void;
  incident: string | null; setIncident: (n: string | null) => void;
  turns: Turn[]; ask: (q: string, opts?: { storeId?: number | null; incidentNumber?: string | null }) => void;
  clearTurns: () => void;
  tab: string; setTab: (t: string) => void; pulseTab: string | null;
  drawer: Drawer; setDrawer: (d: Drawer) => void;
  bannerOpen: boolean; setBannerOpen: (b: boolean) => void;
  blastAnchor: string | null; setBlastAnchor: (n: string | null) => void;
  resetDemo: () => void;
};
const C = createContext<Ctx | null>(null);
export const useApp = () => useContext(C)!;

const PANEL_TAB: Record<string, string> = { timeline: "timeline", similar: "fixes", changes: "changes", blast: "blast", impact: "impact" };

export function AppProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const { data: meta } = useQuery({ queryKey: ["meta"], queryFn: () => api("/api/meta"), refetchInterval: 15000 });
  const [personaId, setPid] = useState<string>(() => { try { return localStorage.getItem("sl.persona") || ""; } catch { return ""; } });
  const [theme, setTheme] = useState<"dark" | "light">(() => { try { return (localStorage.getItem("sl.theme") as any) || "dark"; } catch { return "dark"; } });
  const [storeId, setStoreId] = useState<number | null>(null);
  const [incident, setIncident] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [tab, setTab] = useState("timeline");
  const [pulseTab, setPulse] = useState<string | null>(null);
  const [drawer, setDrawer] = useState<Drawer>(null);
  const [bannerOpen, setBannerOpen] = useState(true);
  const [blastAnchor, setBlastAnchor] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const turnsRef = useRef<Turn[]>([]);
  turnsRef.current = turns;

  const personas: Persona[] = meta?.personas ?? [];
  const resolvedId = personaId || meta?.default_personas?.engineer || "";
  setPersonaId(resolvedId);
  const persona = personas.find((p) => p.user_id === resolvedId) ?? null;

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    try { localStorage.setItem("sl.theme", theme); } catch { /* ignore */ }
  }, [theme]);

  const ask = useCallback((question: string, opts: { storeId?: number | null; incidentNumber?: string | null } = {}) => {
    const sid = opts.storeId !== undefined ? opts.storeId : storeId;
    const inc = opts.incidentNumber !== undefined ? opts.incidentNumber : incident;
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    const id = Math.random().toString(36).slice(2);
    const t: Turn = { id, question, storeId: sid, incidentNumber: inc, persona: resolvedId, partial: { headline: "", bullets: [] },
      tools: [], status: "running", startedAt: Date.now() };
    setTurns((ts) => [...ts.slice(-9), t]);
    const upd = (f: (t: Turn) => Turn) => setTurns((ts) => ts.map((x) => (x.id === id ? f(x) : x)));
    const history = turnsRef.current.slice(-3).flatMap((x) => x.answer ? [
      { role: "user", content: x.question },
      { role: "assistant", content: [x.answer.headline, ...x.answer.bullets.map((b) => "- " + b.text)].join("\n") }] : []);
    chatStream({ question, store_id: sid, incident_number: inc, history }, (e) => {
      if (e.event === "tool_start") upd((t) => ({ ...t, tools: [...t.tools, { name: e.data.name, label: e.data.label, done: false, args: e.data.args }] }));
      else if (e.event === "tool_end") upd((t) => {
        const i = t.tools.findIndex((x) => x.name === e.data.name && !x.done);
        const tools = [...t.tools];
        if (i >= 0) tools[i] = { ...tools[i], done: true, ms: e.data.ms, rows: e.data.rows, masked_fields: e.data.masked_fields };
        return { ...t, tools };
      });
      else if (e.event === "delta") upd((t) => {
        const p = { headline: t.partial.headline, bullets: [...t.partial.bullets] };
        if (e.data.kind === "headline") p.headline += e.data.text;
        else if (e.data.kind === "bullet") {
          const i = e.data.i;
          p.bullets[i] = { text: (p.bullets[i]?.text ?? "") + e.data.text, citations: e.data.citations ?? [] };
        }
        return { ...t, partial: p };
      });
      else if (e.event === "answer") {
        upd((t) => ({ ...t, answer: e.data.answer, validation: e.data.validation, mode: e.data.mode }));
        const panels: string[] = e.data.answer?.panels ?? [];
        const first = panels.map((p) => PANEL_TAB[p]).find(Boolean);
        if (first) { setTab(first); setPulse(first); setTimeout(() => setPulse(null), 1800); }
      } else if (e.event === "trace") upd((t) => ({ ...t, trace: e.data, status: "done" }));
      else if (e.event === "error") upd((t) => ({ ...t, status: "error", error: e.data.error }));
    }, ac.signal).catch((err) => { if (err?.name !== "AbortError") upd((t) => ({ ...t, status: "error", error: String(err) })); });
  }, [storeId, incident, resolvedId]);

  const setPersona = useCallback((id: string) => {
    setPid(id);
    try { localStorage.setItem("sl.persona", id); } catch { /* ignore */ }
    setPersonaId(id);
    qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== "meta" });
    const last = turnsRef.current[turnsRef.current.length - 1];
    setTurns([]); // never leave another persona's answers on screen
    setDrawer(null);
    if (last) setTimeout(() => ask(last.question, { storeId: last.storeId, incidentNumber: last.incidentNumber }), 50);
  }, [qc, ask]);

  const resetDemo = useCallback(() => {
    abortRef.current?.abort();
    setTurns([]); setStoreId(null); setIncident(null); setDrawer(null); setBannerOpen(true); setTab("timeline");
    setBlastAnchor(null);
    if (meta?.default_personas?.engineer) { setPid(meta.default_personas.engineer); setPersonaId(meta.default_personas.engineer); }
    api("/api/admin/reset", { method: "POST" }).catch(() => {});
    qc.invalidateQueries();
  }, [meta, qc]);

  const v = useMemo<Ctx>(() => ({
    meta, personas, persona, setPersona, theme, toggleTheme: () => setTheme((t) => (t === "dark" ? "light" : "dark")),
    storeId, setStoreId, incident, setIncident, turns, ask, clearTurns: () => setTurns([]), tab, setTab, pulseTab,
    drawer, setDrawer, bannerOpen, setBannerOpen, blastAnchor, setBlastAnchor, resetDemo,
  }), [meta, personas, persona, setPersona, theme, storeId, incident, turns, ask, tab, pulseTab, drawer, bannerOpen, blastAnchor, resetDemo]);
  if (!meta || !persona) return <div className="flex h-full items-center justify-center text-muted">Loading StoreLens…</div>;
  return <C.Provider value={v}>{children}</C.Provider>;
}

/** Live $ ticker: base + per_hour × elapsed wall time (the simulated clock runs at wall speed). */
export function useTicker(base: number | null | undefined, perHour: number | null | undefined, key: any) {
  const [v, setV] = useState<number | null>(base ?? null);
  useEffect(() => {
    if (base === null || base === undefined) { setV(null); return; }
    const t0 = Date.now();
    setV(base);
    if (!perHour) return;
    const iv = setInterval(() => setV(base + (perHour * (Date.now() - t0)) / 3_600_000), 500);
    return () => clearInterval(iv);
  }, [base, perHour, key]);
  return v;
}
