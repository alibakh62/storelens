import { useMutation } from "@tanstack/react-query";
import { Copy, Download, FileText, Loader2 } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { Modal, Toast } from "./Teams";

export function Markdown({ md }: { md: string }) {
  const lines = md.split("\n");
  const out: JSX.Element[] = [];
  let list: string[] = [];
  const inline = (s: string) => s.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/_(.+?)_/g, "<i>$1</i>");
  const flush = () => { if (list.length) { out.push(<ul key={out.length}>{list.map((l, i) => <li key={i} dangerouslySetInnerHTML={{ __html: inline(esc(l)) }} />)}</ul>); list = []; } };
  for (const ln of lines) {
    if (ln.startsWith("- ")) { list.push(ln.slice(2)); continue; }
    flush();
    if (ln.startsWith("# ")) out.push(<h1 key={out.length}>{ln.slice(2)}</h1>);
    else if (ln.startsWith("## ")) out.push(<h2 key={out.length}>{ln.slice(3)}</h2>);
    else if (ln.trim()) out.push(<p key={out.length} dangerouslySetInnerHTML={{ __html: inline(esc(ln)) }} />);
  }
  flush();
  return <div className="md text-sm leading-relaxed">{out}</div>;
}
const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

export function download(name: string, text: string, type = "text/markdown") {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name;
  a.click();
}

export function PirButton({ num }: { num: string }) {
  const [open, setOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const m = useMutation({ mutationFn: () => api(`/api/pir/${num}`, { method: "POST" }) });
  return (
    <>
      <button className="btn text-xs" data-testid="pir-button" onClick={() => { setOpen(true); m.mutate(); }}><FileText size={13} /> Draft incident summary</button>
      {open && (
        <Modal title={m.data?.title ?? "Incident summary"} onClose={() => setOpen(false)} wide>
          {m.isPending ? <div className="flex items-center gap-2 text-sm text-muted"><Loader2 size={14} className="animate-spin" /> Gathering facts…</div> :
            m.data ? (<>
              <div className="mb-3 flex gap-2">
                <button className="btn text-xs" onClick={() => { navigator.clipboard?.writeText(m.data.markdown); setToast("Copied to clipboard"); }}><Copy size={13} /> Copy</button>
                <button className="btn text-xs" onClick={() => download(`${num}_summary.md`, m.data.markdown)}><Download size={13} /> Download .md</button>
                <span className="ml-auto chip">from cited facts · {m.data.mode}</span>
              </div>
              <div className="rounded-lg border border-border bg-surface2/30 p-4"><Markdown md={m.data.markdown} /></div>
            </>) : <div className="text-sm text-crit">{String(m.error)}</div>}
          {toast && <Toast text={toast} onDone={() => setToast(null)} />}
        </Modal>)}
    </>
  );
}
