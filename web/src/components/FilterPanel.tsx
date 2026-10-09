import { useMemo, useState } from "react";
import type { Meta } from "../api";
import { label } from "../format";

interface Props {
  meta: Meta;
  params: URLSearchParams;
  onChange: (next: URLSearchParams) => void;
}

/** All filters live in the URL so a territory view can be bookmarked and shared with the team. */
export function FilterPanel({ meta, params, onChange }: Props) {
  const [countryQuery, setCountryQuery] = useState("");
  const f = meta.facets;

  const update = (mutate: (p: URLSearchParams) => void) => {
    const next = new URLSearchParams(params);
    mutate(next);
    next.delete("page");
    onChange(next);
  };
  const toggle = (key: string, value: string) =>
    update((p) => {
      const values = p.getAll(key);
      p.delete(key);
      (values.includes(value) ? values.filter((v) => v !== value) : [...values, value]).forEach((v) => p.append(key, v));
    });
  const selected = (key: string, value: string) => params.getAll(key).includes(value);

  const regions = params.getAll("region");
  const countries = useMemo(
    () => f.countries
      .filter((c) => !regions.length || regions.includes(c.region))
      .filter((c) => !countryQuery || c.label?.toLowerCase().includes(countryQuery.toLowerCase())
        || c.value.toLowerCase() === countryQuery.toLowerCase())
      .slice(0, 12),
    [f.countries, regions, countryQuery],
  );

  const group = (title: string, key: string, values: { value: string; count: number }[]) => (
    <fieldset className="filter-group">
      <legend>{title}</legend>
      {values.map((v) => (
        <label key={v.value} className="check">
          <input type="checkbox" checked={selected(key, v.value)} onChange={() => toggle(key, v.value)} />
          <span>{label(v.value)}</span>
          <em>{v.count.toLocaleString()}</em>
        </label>
      ))}
    </fieldset>
  );

  const activeCount = [...params.keys()].filter((k) => !["sort", "page"].includes(k)).length;

  return (
    <aside className="filters">
      <div className="filters-head">
        <h2>Filters</h2>
        {activeCount > 0 && <button className="link" onClick={() => onChange(new URLSearchParams())}>Clear all</button>}
      </div>

      <input className="search" placeholder="Search account or domain…" defaultValue={params.get("q") ?? ""}
        onKeyDown={(e) => {
          if (e.key === "Enter") update((p) => {
            const v = (e.target as HTMLInputElement).value.trim();
            if (v) p.set("q", v); else p.delete("q");
          });
        }} />

      {group("Tier", "tier", [...f.tiers].sort((a, b) => a.value.localeCompare(b.value)))}

      <fieldset className="filter-group">
        <legend>Buying signals <span className="hint">(must have all)</span></legend>
        <div className="chips-wrap">
          {f.signals.filter((s) => s.count > 0).map((s) => (
            <button key={s.key} className={`chip-toggle cat-${s.category} ${selected("signal", s.key) ? "on" : ""}`}
              onClick={() => toggle("signal", s.key)} title={`${s.count.toLocaleString()} accounts · weight ${s.weight}`}>
              {s.label}
            </button>
          ))}
        </div>
      </fieldset>

      {group("Region", "region", f.regions)}

      <fieldset className="filter-group">
        <legend>Country</legend>
        <input className="search small" placeholder="Find country…" value={countryQuery}
          onChange={(e) => setCountryQuery(e.target.value)} />
        {params.getAll("country").filter((c) => !countries.some((x) => x.value === c)).map((c) => (
          <label key={c} className="check"><input type="checkbox" checked onChange={() => toggle("country", c)} /><span>{c}</span></label>
        ))}
        {countries.map((c) => (
          <label key={c.value} className="check">
            <input type="checkbox" checked={selected("country", c.value)} onChange={() => toggle("country", c.value)} />
            <span>{c.label ?? c.value}</span><em>{c.count.toLocaleString()}</em>
          </label>
        ))}
      </fieldset>

      {group("Industry", "industry", f.industries)}
      {group("Size (exposed footprint)", "segment", f.segments)}
      {group("Hosting", "hosting", f.hosting)}

      <fieldset className="filter-group">
        <legend>Minimum need: {params.get("min_need") ?? 0}</legend>
        <input type="range" min={0} max={100} step={5} defaultValue={params.get("min_need") ?? "0"}
          onMouseUp={(e) => update((p) => {
            const v = (e.target as HTMLInputElement).value;
            if (v === "0") p.delete("min_need"); else p.set("min_need", v);
          })}
          onKeyUp={(e) => update((p) => p.set("min_need", (e.target as HTMLInputElement).value))} />
      </fieldset>

      <fieldset className="filter-group">
        <legend>Scope</legend>
        <label className="check">
          <input type="checkbox" checked={params.get("prospects_only") === "false"}
            onChange={(e) => update((p) => { if (e.target.checked) p.set("prospects_only", "false"); else p.delete("prospects_only"); })} />
          <span>Include non-prospects (hosting/ISP)</span>
        </label>
      </fieldset>
    </aside>
  );
}
