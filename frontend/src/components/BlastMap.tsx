import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useEffect, useMemo, useState } from "react";
import { CircleMarker, GeoJSON, MapContainer, Tooltip, useMap } from "react-leaflet";
import { feature } from "topojson-client";
import states from "us-atlas/states-10m.json";
import { useApp } from "../lib/store";

// Local US states GeoJSON (no tile server — works offline)
const GEO: any = feature(states as any, (states as any).objects.states);

function Fit({ bbox }: { bbox: [[number, number], [number, number]] | null }) {
  const map = useMap();
  useEffect(() => {
    if (!bbox) return;
    const b = L.latLngBounds(bbox[0], bbox[1]).pad(1.2);
    map.fitBounds(b, { animate: true, duration: 0.6, maxZoom: 8 });
  }, [bbox, map]);
  return null;
}

export function BlastMap({ data }: { data: any }) {
  const { theme, setDrawer } = useApp();
  const [shown, setShown] = useState(0);
  const stores = useMemo(() => [...(data?.stores ?? [])].filter((s: any) => s.in_cluster || s.anchor)
    .sort((a: any, b: any) => (a.dist_km ?? 0) - (b.dist_km ?? 0)), [data]);
  // ripple: markers pulse in by distance from the anchor store
  useEffect(() => {
    setShown(0);
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduce) { setShown(stores.length); return; }
    let i = 0;
    const iv = setInterval(() => { i += Math.max(1, Math.ceil(stores.length / 30)); setShown(i); if (i >= stores.length) clearInterval(iv); }, 40);
    return () => clearInterval(iv);
  }, [stores]);
  const dark = theme === "dark";
  const bbox = data?.geo?.bbox ?? null;
  return (
    <div className="relative h-[300px] overflow-hidden rounded-lg border border-border bg-surface2/40" data-testid="blast-map">
      <MapContainer center={[37.5, -96]} zoom={4} zoomControl={false} attributionControl={false} style={{ height: "100%", width: "100%" }} scrollWheelZoom={false}>
        <GeoJSON data={GEO} style={{ color: dark ? "#2c3a57" : "#c4cddb", weight: 1, fillColor: dark ? "#141b2b" : "#eef2f8", fillOpacity: 1 }} />
        <Fit bbox={bbox} />
        {stores.slice(0, shown).map((s: any) => {
          const col = s.status === "offline" ? "#F0523D" : "#F5A524";
          return (
            <CircleMarker key={s.store_id} center={[s.lat, s.lon]} radius={s.anchor ? 9 : 5.5}
              pathOptions={{ color: s.anchor ? (dark ? "#E6EAF2" : "#111827") : col, weight: s.anchor ? 3 : 1, fillColor: col, fillOpacity: 0.85 }}
              eventHandlers={{ click: () => setDrawer({ kind: "record", id: s.incident_number }) }}>
              <Tooltip direction="top" offset={[0, -4]}>
                <div className="text-xs"><b>Store {s.store_id}</b>{s.anchor ? " (this store)" : ""}<br />{s.market} · {s.status}<br /><span className="font-mono">{s.incident_number}</span></div>
              </Tooltip>
            </CircleMarker>);
        })}
      </MapContainer>
      <div className="pointer-events-none absolute bottom-2 left-2 flex gap-3 rounded-md bg-surface/90 px-2 py-1 text-[11px]">
        <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full bg-crit" /> offline</span>
        <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full bg-warn" /> degraded</span>
        <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full border-2 border-text bg-crit" /> this store</span>
      </div>
    </div>
  );
}
