import * as Dialog from "@radix-ui/react-dialog";
import * as AC from "adaptivecards";
import { Send, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../lib/api";
import { type Answer, useApp } from "../lib/store";

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  return (
    <Dialog.Root open onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Dialog.Content className={`fixed left-1/2 top-1/2 z-50 max-h-[88vh] w-[94vw] -translate-x-1/2 -translate-y-1/2 overflow-auto rounded-xl border border-border bg-surface p-5 shadow-2xl scroll-thin ${wide ? "max-w-4xl" : "max-w-2xl"}`}>
          <div className="mb-3 flex items-center justify-between">
            <Dialog.Title className="text-base font-semibold">{title}</Dialog.Title>
            <Dialog.Close className="rounded p-1 text-muted hover:bg-surface2" aria-label="Close"><X size={16} /></Dialog.Close>
          </div>
          <Dialog.Description className="sr-only">{title}</Dialog.Description>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function Toast({ text, onDone }: { text: string; onDone: () => void }) {
  useEffect(() => { const t = setTimeout(onDone, 2600); return () => clearTimeout(t); }, [onDone]);
  return <div role="status" className="fixed bottom-6 left-1/2 z-[60] -translate-x-1/2 rounded-lg border border-accent/50 bg-surface px-4 py-2 text-sm shadow-xl">{text}</div>;
}

export function AdaptiveCardView({ card }: { card: any }) {
  const ref = useRef<HTMLDivElement>(null);
  const { theme } = useApp();
  useEffect(() => {
    if (!ref.current || !card) return;
    const ac = new AC.AdaptiveCard();
    const dark = theme === "dark";
    ac.hostConfig = new AC.HostConfig({
      fontFamily: "IBM Plex Sans, Segoe UI, sans-serif",
      containerStyles: { default: { backgroundColor: dark ? "#1f2433" : "#ffffff", foregroundColors: {
        default: { default: dark ? "#E6EAF2" : "#111827", subtle: dark ? "#8A96AD" : "#5c6980" },
        attention: { default: "#F0523D" }, warning: { default: "#F5A524" }, good: { default: "#18B67A" }, accent: { default: "#4C8DFF" } } } },
      actions: { actionsOrientation: "horizontal", buttonSpacing: 8 },
    });
    ac.parse(card);
    const el = ac.render();
    ref.current.innerHTML = "";
    if (el) ref.current.appendChild(el);
  }, [card, theme]);
  return <div ref={ref} className="ac-host [&_button]:rounded-md [&_button]:border [&_button]:border-border [&_button]:px-3 [&_button]:py-1 [&_button]:text-sm" />;
}

/** Teams-like chat frame (generic styling, no logos). Sending is simulated. */
export function TeamsFrame({ card, channel }: { card: any; channel: string }) {
  const [toast, setToast] = useState<string | null>(null);
  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <div className="flex items-center gap-2 border-b border-border bg-surface2 px-3 py-2 text-sm"><span className="h-2 w-2 rounded-full bg-accent" /> <span className="font-medium">#{channel}</span><span className="text-xs text-muted">· chat preview (simulated)</span></div>
      <div className="space-y-2 bg-bg/60 p-4">
        <div className="flex items-center gap-2 text-xs text-muted"><span className="flex h-6 w-6 items-center justify-center rounded bg-accent/20 text-[10px] font-bold text-accent">SL</span> StoreLens bot · now</div>
        <div className="max-w-xl rounded-lg border border-border">{card ? <AdaptiveCardView card={card} /> : <div className="p-4 text-sm text-muted">Rendering…</div>}</div>
      </div>
      <div className="flex justify-end border-t border-border bg-surface2 px-3 py-2">
        <button className="btn-primary btn" onClick={() => setToast(`Sent to #${channel} (simulated)`)}><Send size={14} /> Send</button>
      </div>
      {toast && <Toast text={toast} onDone={() => setToast(null)} />}
    </div>
  );
}

export function TeamsPreview({ onClose, answer, storeId }: { onClose: () => void; answer: Answer; storeId: number | null }) {
  const [card, setCard] = useState<any>(null);
  const { persona } = useApp();
  useEffect(() => {
    const parent = (answer.bullets.flatMap((b) => b.citations).find((c) => c === "INC0039143")) ??
      answer.bullets.flatMap((b) => b.text.match(/major (INC\d{7})/)?.[1] ?? []).find(Boolean) ?? null;
    api("/api/teams/card", { method: "POST", body: JSON.stringify({ headline: answer.headline, bullets: answer.bullets.slice(0, 3).map((b) => b.text),
      impact: answer.impact, status: answer.status, parent, store_id: storeId }) }).then(setCard).catch(() => {});
  }, [answer, storeId]);
  const ch = persona?.region_scope && persona.region_scope !== "All" ? `store-ops-${persona.region_scope.toLowerCase()}` : "store-ops-texas";
  return <Modal title="Preview in Teams" onClose={onClose}><TeamsFrame card={card} channel={ch} /></Modal>;
}
