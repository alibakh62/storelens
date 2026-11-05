export const TZ: Record<number, string> = { [-5]: "ET", [-6]: "CT", [-7]: "MT", [-8]: "PT" };
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function money(x: number | null | undefined, opts: { compact?: boolean } = {}) {
  if (x === null || x === undefined) return "—";
  const c = opts.compact ?? true;
  if (c && Math.abs(x) >= 1e6) return `$${(x / 1e6).toFixed(1)}M`;
  if (c && Math.abs(x) >= 1e4) return `$${(x / 1e3).toFixed(1)}k`;
  return `$${Math.round(x).toLocaleString("en-US")}`;
}

export function parseUtc(iso: string) {
  return new Date(iso.replace(/(\.\d{3})\d+/, "$1") + (/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? "" : "Z"));
}

// Naive-UTC ISO string -> local time at a fixed offset (no DST, consistent with the generator)
export function localTime(iso?: string | null, off = -6, withDate = false) {
  if (!iso) return "—";
  const t = new Date(parseUtc(iso).getTime() + off * 3600_000);
  const h = t.getUTCHours(), m = t.getUTCMinutes();
  const time = `${((h + 11) % 12) + 1}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
  if (!withDate) return `${time} ${TZ[off] ?? ""}`.trim();
  return `${DAYS[t.getUTCDay()]} ${MONS[t.getUTCMonth()]} ${t.getUTCDate()}, ${time} ${TZ[off] ?? ""}`.trim();
}

export function shortDate(iso?: string | null, off = -6) {
  if (!iso) return "—";
  const t = new Date(parseUtc(iso).getTime() + off * 3600_000);
  return `${MONS[t.getUTCMonth()]} ${t.getUTCDate()}`;
}

export function hours(h?: number | null) {
  if (h === null || h === undefined) return "—";
  if (h < 1) return `${Math.round(h * 60)} min`;
  if (h >= 48) return `${Math.round(h)}h`;
  return `${h.toFixed(1)}h`;
}

export function pct(x?: number | null, d = 0) {
  return x === null || x === undefined ? "—" : `${(x * 100).toFixed(d)}%`;
}

export function cls(...xs: (string | false | null | undefined)[]) { return xs.filter(Boolean).join(" "); }

export const ID_RX = /\b(?:INC|CHG|PRB|KB)\d{7}\b/g;
