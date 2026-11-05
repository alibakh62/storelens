import { AnimatePresence, motion } from "framer-motion";
import { Activity, ArrowUp, Check, Loader2, MessageSquareShare, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { cls, localTime } from "../lib/format";
import { type Answer, type Turn, useApp } from "../lib/store";
import { TeamsPreview } from "./Teams";
import { CitationChip, CitedText, Footnote, STATUS, Ticker } from "./ui";

export const SUGGESTED: Record<string, string[]> = {
  engineer: ["What's going on at this store?", "Is this affecting other stores?", "What fixed this last time?", "Did anything change here recently?"],
  exec: ["What's going on at this store?", "How much is the DFW outage costing us?", "Is this affecting other stores?", "What should I tell stakeholders?"],
  manager: ["What's going on at this store?", "Is this affecting other stores?", "Which of my stores are at SLA risk?", "Shift handoff for Texas"],
};

function ToolChips({ t }: { t: Turn }) {
  if (!t.tools.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5" data-testid="tool-chips">
      {t.tools.map((x, i) => (
        <motion.span key={i} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.15 }}
          className={cls("chip", x.done ? "text-muted" : "border-info/50 text-info")}>
          {x.done ? <Check size={12} className="text-accent" /> : <Loader2 size={12} className="animate-spin" />}{x.label.replace("…", x.done ? "" : "…")}
          {x.done && x.ms !== undefined && <span className="font-mono text-[10px] opacity-60">{Math.round(x.ms)}ms</span>}
        </motion.span>))}
    </div>
  );
}

function Bullets({ bullets, caret }: { bullets: { text: string; citations: string[] }[]; caret?: boolean }) {
  return (
    <ul className="space-y-2">
      {bullets.map((b, i) => {
        const inline = new Set((b.text.match(/\b(?:INC|CHG|PRB|KB)\d{7}\b/g) ?? []));
        const extra = (b.citations ?? []).filter((c) => !inline.has(c));
        return (
          <li key={i} className={cls("flex gap-2 leading-relaxed", caret && i === bullets.length - 1 && "caret")}>
            <span className="mt-[9px] h-1.5 w-1.5 shrink-0 rounded-full bg-muted" aria-hidden />
            <span><CitedText text={b.text} />{extra.length > 0 && <span className="ml-1 whitespace-nowrap">{extra.slice(0, 4).map((c) => <CitationChip key={c} id={c} />)}</span>}</span>
          </li>);
      })}
    </ul>
  );
}

export function AnswerView({ t, a }: { t: Turn; a: Answer }) {
  const { ask, setDrawer, storeId } = useApp();
  const [teams, setTeams] = useState(false);
  const st = STATUS[a.status] ?? STATUS.info;
  const I = st.icon;
  const imp = a.impact;
  return (
    <div className="space-y-3" data-testid="answer">
      <div className={cls("flex items-start gap-2 rounded-lg border px-3 py-2", st.border, st.bg)}>
        <I size={18} className={cls("mt-0.5 shrink-0", st.color)} aria-label={st.label} />
        <h3 className="text-base font-semibold leading-snug" data-testid="answer-headline"><CitedText text={a.headline} /></h3>
      </div>
      {imp && (imp.usd_so_far !== null || imp.restricted) && (
        <div className="flex items-center gap-4 rounded-lg border border-money/30 bg-money/5 px-3 py-2" data-testid="impact-strip">
          <div><div className="label">Est. lost sales {imp.scope === "major" ? "(all affected stores)" : "(this store, open)"}</div>
            <div className="text-xl font-semibold"><Ticker base={imp.usd_so_far} perHour={imp.per_hour_now} k={imp.as_of} /></div></div>
          {imp.per_hour_now !== null && imp.per_hour_now !== undefined && <div><div className="label">Per hour now</div><div className="tabular text-money">${Math.round(imp.per_hour_now).toLocaleString()}/h</div></div>}
          <div className="ml-auto text-right text-[11px] text-muted">as of {localTime(imp.as_of)} <Footnote /></div>
        </div>)}
      <Bullets bullets={a.bullets} />
      {a.sections.map((s) => (
        <div key={s.title} className="rounded-lg border border-border bg-surface2/40 p-3">
          <div className="label mb-1.5">{s.title}</div><Bullets bullets={s.bullets} />
        </div>))}
      {a.recommended_actions.length > 0 && (
        <div className="rounded-lg border border-accent/30 bg-accent/5 p-3">
          <div className="label mb-1.5">Recommended actions</div>
          <ul className="space-y-1.5">{a.recommended_actions.map((x, i) => (
            <li key={i} className="flex gap-2"><Sparkles size={14} className="mt-1 shrink-0 text-accent" />
              <span><CitedText text={x.text} />{x.owner && <span className="ml-1 chip">{x.owner}</span>}</span></li>))}</ul>
        </div>)}
      <div className="flex flex-wrap items-center gap-1.5">
        {a.followups.map((f) => <button key={f} className="btn text-xs" data-testid="followup" onClick={() => ask(f)}>{f}</button>)}
        <span className="ml-auto flex items-center gap-1">
          <span className={cls("chip", a.confidence === "low" ? "text-warn" : "text-muted")} title="Confidence">confidence: {a.confidence}</span>
          <button className="btn px-2 py-1 text-xs" onClick={() => setTeams(true)} title="Preview in Teams"><MessageSquareShare size={13} /> Teams</button>
          <button className="btn px-2 py-1 text-xs" onClick={() => setDrawer({ kind: "trace", turnId: t.id })} data-testid="open-trace" title="Trace"><Activity size={13} /> Trace</button>
        </span>
      </div>
      {teams && <TeamsPreview onClose={() => setTeams(false)} answer={a} storeId={storeId} />}
    </div>
  );
}

function TurnView({ t, latest }: { t: Turn; latest: boolean }) {
  return (
    <motion.div data-turn initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2 }} className={cls("space-y-3", !latest && "opacity-60")}>
      <div className="flex justify-end"><div className="max-w-[85%] rounded-xl rounded-br-sm bg-info/15 px-3 py-2 text-sm">{t.question}</div></div>
      <ToolChips t={t} />
      {t.answer ? <AnswerView t={t} a={t.answer} /> : t.status === "error" ? <div className="text-sm text-crit">Something went wrong: {t.error}</div> : (
        <div className="space-y-2">
          {t.partial.headline && <h3 className="text-base font-semibold">{t.partial.headline}</h3>}
          <Bullets bullets={t.partial.bullets} caret />
          {!t.partial.headline && <div className="flex items-center gap-2 text-sm text-muted"><Loader2 size={14} className="animate-spin" /> Thinking…</div>}
        </div>)}
    </motion.div>
  );
}

export function AnswerPane({ storeId }: { storeId: number | null }) {
  const { turns, ask, persona } = useApp();
  const [q, setQ] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const shown = turns.filter((t) => t.storeId === storeId || storeId === null);
  const lastT = shown[shown.length - 1];
  useEffect(() => {
    const el = box.current;
    if (!el || !lastT) return;
    // keep the latest question in view while it streams (scroll inside the pane only)
    const q = el.querySelector<HTMLElement>("[data-turn]:last-of-type");
    el.scrollTo({ top: q ? q.offsetTop - 8 : el.scrollHeight, behavior: "smooth" });
  }, [shown.length, lastT?.answer, lastT?.tools.length]);
  const sugg = SUGGESTED[persona?.persona ?? "engineer"];
  const submit = (v: string) => { if (!v.trim()) return; ask(v.trim()); setQ(""); };
  return (
    <section className="card flex h-full min-h-[560px] flex-col overflow-hidden" aria-label="Answer">
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <h2 className="label">Ask StoreLens</h2>
        <span className="text-[11px] text-muted">AI decides what to look at · code computes every number</span>
      </div>
      <div ref={box} className="relative flex-1 space-y-6 overflow-auto p-4 scroll-thin" data-testid="answer-pane">
        {shown.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-center text-muted">
            <Sparkles className="text-accent" />
            <div className="max-w-sm text-sm">Ask anything about {storeId ? `store ${storeId}` : "a store"}. Every line is cited to an INC / CHG / PRB / KB record.</div>
          </div>)}
        <AnimatePresence initial={false}>{shown.map((t, i) => <TurnView key={t.id} t={t} latest={i === shown.length - 1} />)}</AnimatePresence>
      </div>
      <div className="border-t border-border p-3">
        <div className="mb-2 flex flex-wrap gap-1.5">{sugg.map((s) => <button key={s} className="btn text-xs" data-testid="suggestion" onClick={() => submit(s)}>{s}</button>)}</div>
        <form onSubmit={(e) => { e.preventDefault(); submit(q); }} className="flex items-center gap-2 rounded-xl border border-border bg-surface2/50 px-3 py-2 focus-within:border-info">
          <input data-testid="ask-input" value={q} onChange={(e) => setQ(e.target.value)} placeholder={storeId ? `Ask about store ${storeId}…` : "Ask a question…"} className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted" aria-label="Ask a question" />
          <button className="rounded-lg bg-info p-1.5 text-white disabled:opacity-40" disabled={!q.trim()} aria-label="Send"><ArrowUp size={15} /></button>
        </form>
      </div>
    </section>
  );
}
