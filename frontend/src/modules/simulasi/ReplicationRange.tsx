import type { SimulationRange, SimulationValue } from './simulationTypes';
export const formatValue = (v: SimulationValue | undefined) => v == null ? '—' : typeof v === 'number' ? new Intl.NumberFormat('id-ID', {maximumFractionDigits: 2}).format(v) : /^\d{4}-\d{2}-\d{2}$/.test(v) && !Number.isNaN(Date.parse(v)) ? new Intl.DateTimeFormat('id-ID',{day:'numeric',month:'short',year:'numeric',timeZone:'UTC'}).format(new Date(v)) : v;
export function ReplicationRange({range, format = formatValue}: {range: SimulationRange | null | undefined; format?: (v: SimulationValue) => string}) {
 if (!range) return <span>—</span>;
 return <div className="sr-range"><small>Perkiraan tengah (P50)</small><strong>{format(range.median)}</strong><small>Rentang perkiraan (P10–P90): {format(range.p10)} – {format(range.p90)}</small><small>Terendah–tertinggi: {format(range.min)} – {format(range.max)}</small><small>Hasil tidak tersedia: {range.missing} / {range.runs}</small></div>;
}

export function RangeComparison({baseline, scenario, format = formatValue, extra = []}: {baseline: SimulationRange | null | undefined; scenario: SimulationRange | null | undefined; format?: (v: SimulationValue) => string; extra?: [string,string,string][]}) {
 const rows: [string,string,string][] = [
  ['Perkiraan tengah (P50)', baseline ? format(baseline.median) : '—', scenario ? format(scenario.median) : '—'],
  ['Rentang perkiraan (P10–P90)', baseline ? `${format(baseline.p10)} – ${format(baseline.p90)}` : '—', scenario ? `${format(scenario.p10)} – ${format(scenario.p90)}` : '—'],
  ['Terendah–tertinggi', baseline ? `${format(baseline.min)} – ${format(baseline.max)}` : '—', scenario ? `${format(scenario.min)} – ${format(scenario.max)}` : '—'],
  ['Hasil tidak tersedia', baseline ? `${baseline.missing}/${baseline.runs}` : '—', scenario ? `${scenario.missing}/${scenario.runs}` : '—'], ...extra
 ];
 return <table className="sr-comparison"><thead><tr><th scope="col">Statistik</th><th scope="col">Kondisi awal</th><th scope="col">Skenario</th></tr></thead><tbody>{rows.map(([label,b,s])=><tr key={label}><th scope="row">{label}</th><td>{b}</td><td>{s}</td></tr>)}</tbody></table>;
}
