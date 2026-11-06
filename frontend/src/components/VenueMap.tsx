// Venue options: a small OpenStreetMap with one numbered pin per located candidate.
// Pins only for real addresses (from the venue page, geocoded by the backend). No photos.
import "leaflet/dist/leaflet.css";
import L from "leaflet";
import { ExternalLink, MapPin } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { MapContainer, Marker, TileLayer, Tooltip, useMap } from "react-leaflet";
import { api } from "../api";
import { useRipple } from "../store";
import type { Venues, View } from "../types";
import { CardHead } from "./ui";

function pin(n: string, kind: "candidate" | "chosen" | "lost") {
  return L.divIcon({ className: "", html: `<span class="pin pin-${kind}">${n}</span>`, iconSize: [28, 28], iconAnchor: [14, 14] });
}

function FitBounds({ points }: { points: [number, number][] }) {
  const map = useMap();
  useEffect(() => {
    if (points.length === 1) map.setView(points[0], 14);
    else if (points.length > 1) map.fitBounds(L.latLngBounds(points), { padding: [36, 36] });
  }, [map, points]);
  return null;
}

const core = (s: string) => s.split(/[(–-]/)[0].trim().toLowerCase();

export function VenueCard({ view }: { view: View }) {
  const { busy } = useRipple();
  const [data, setData] = useState<Venues | null>(null);
  const [error, setError] = useState(false);
  const hasCandidates = (view.candidates ?? []).length > 0;

  useEffect(() => {
    if (!hasCandidates || busy) return;   // the venue list is saved when the run ends
    let alive = true;
    api.venues(view.id).then((d) => alive && setData(d)).catch(() => alive && setError(true));
    return () => { alive = false; };
  }, [view.id, hasCandidates, busy]);

  // the venue of the chosen (or best) option, to highlight its pin
  const pick = view.scenarios?.find((s) => s.id === (view.chosen ?? view.scored?.find((x) => x.feasible)?.id));
  const pickName = pick?.changes?.find((c) => c.replace_with)?.replace_with;
  const isPick = (name: string) => !!pickName && (core(pickName).includes(core(name)) || core(name).includes(core(pickName)));

  const located = useMemo(() => (data?.candidates ?? []).map((c, i) => ({ ...c, n: i + 1 })).filter((c) => c.lat !== null && c.lon !== null), [data]);
  const points = useMemo(() => {
    const p = located.map((c) => [c.lat!, c.lon!] as [number, number]);
    if (data?.lost?.lat != null && data.lost.lon != null) p.push([data.lost.lat, data.lost.lon]);
    return p;
  }, [located, data]);

  if (!hasCandidates) return null;
  const list = data?.candidates ?? (view.candidates ?? []).map((c) => ({ ...c, lat: null, lon: null }));

  return (
    <section className="card">
      <CardHead icon={<MapPin size={18} />} title="Venue options"
        sub="Found on the web by the Resource Finder. Availability is never assumed: always to confirm." />
      {points.length > 0 && (
        <div className="venue-map">
          <MapContainer center={points[0]} zoom={13} scrollWheelZoom={false} style={{ height: "100%", width: "100%" }}>
            <TileLayer url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' />
            {located.map((c) => (
              <Marker key={c.url + c.name} position={[c.lat!, c.lon!]} icon={pin(String(c.n), isPick(c.name) ? "chosen" : "candidate")}>
                <Tooltip direction="top" offset={[0, -12]}>{c.name}</Tooltip>
              </Marker>
            ))}
            {data?.lost?.lat != null && data.lost.lon != null && (
              <Marker position={[data.lost.lat, data.lost.lon]} icon={pin("×", "lost")}>
                <Tooltip direction="top" offset={[0, -12]}>{data.lost.name} (cancelled)</Tooltip>
              </Marker>
            )}
            <FitBounds points={points} />
          </MapContainer>
        </div>
      )}
      {data?.lost?.note && <p className="fine">{data.lost.name} (cancelled): {data.lost.note}.</p>}
      {error && <p className="fine">The map could not be loaded. The venue list below still works.</p>}
      <ol className="venue-list">
        {list.map((c, i) => (
          <li key={c.url + c.name} className={isPick(c.name) ? "picked" : ""}>
            <span className={`pin pin-${isPick(c.name) ? "chosen" : "candidate"} ${c.lat == null ? "pin-off" : ""}`}>{i + 1}</span>
            <div className="venue-main">
              <div className="venue-name">{c.name}</div>
              <div className="sub">
                {[c.capacity ? `${c.capacity} people` : null, c.address || c.area, c.lat == null && data ? "not on the map (no exact address)" : null]
                  .filter(Boolean).join(" · ")}
              </div>
            </div>
            <a className="btn btn-secondary btn-sm" href={c.url} target="_blank" rel="noreferrer">View venue<ExternalLink size={14} /></a>
          </li>
        ))}
      </ol>
    </section>
  );
}
