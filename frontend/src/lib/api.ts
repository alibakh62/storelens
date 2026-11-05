// API client. Every request carries the persona header (X-Persona-Id) — RBAC is enforced server-side.
let personaId = "";
export function setPersonaId(id: string) { personaId = id; }
export function getPersonaId() { return personaId; }

export class ApiError extends Error {
  status: number;
  constructor(s: number, m: string) { super(m); this.status = s; }
}

export async function api<T = any>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", "X-Persona-Id": personaId, ...(init.headers || {}) },
  });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch { /* ignore */ }
    throw new ApiError(r.status, msg);
  }
  return r.json();
}

export type ChatEvent = { event: string; data: any };

export async function chatStream(body: any, onEvent: (e: ChatEvent) => void, signal?: AbortSignal) {
  const r = await fetch("/api/chat", {
    method: "POST", signal,
    headers: { "Content-Type": "application/json", "X-Persona-Id": personaId },
    body: JSON.stringify(body),
  });
  if (!r.ok || !r.body) throw new ApiError(r.status, "chat failed");
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let m: RegExpExecArray | null;
    const sep = /\r?\n\r?\n/;
    while ((m = sep.exec(buf))) {
      const raw = buf.slice(0, m.index);
      buf = buf.slice(m.index + m[0].length);
      let ev = "message";
      const data: string[] = [];
      for (const line of raw.split(/\r?\n/)) {
        if (line.startsWith("event:")) ev = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (data.length) {
        try { onEvent({ event: ev, data: JSON.parse(data.join("\n")) }); } catch { /* ignore */ }
      }
    }
  }
}
