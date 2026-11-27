import { StatisticsDrawer } from './StatisticsDrawer';
import { useState } from 'react';
import { RangeComparison, formatValue } from './ReplicationRange';
import type { MemberResult } from './simulationTypes';
export const percent = (n: number | null | undefined) => n == null ? '—' : `${Math.round(n * 100)}%`;
/** Resource Load Index (FR-04.3): indeks, 1.0 = threshold Overload Flag bawaan. */
export const rli = (n: number | null | undefined) => n == null ? '—' : n.toFixed(2);
export function heatClass(load: number | null | undefined, overloaded: boolean | undefined) {
  if (overloaded) return 'overload';
  if (load == null) return 'missing';
  if (load === 0) return 'idle';
  if (load >= .9) return 'high';
  if (load >= .5) return 'medium';
  return 'low';
}
export function MemberLoadHeatmap({members,onSelect,runs}:{members:MemberResult[];runs?:number;onSelect:(member:MemberResult)=>void}) {
 const [metric,setMetric] = useState<'load'|'median'|'risk'>('load');
 const [selection,setSelection]=useState<{id:string;week:string}|null>(null);
 const member=members.find(m=>m.resource_id===selection?.id);
 const period=member?.periods.find(p=>p.period_start===selection?.week);
 const detail=runs ? period?.replication : null;
 const active = runs ? metric : 'load';
 const weeks = [...new Set(members.flatMap(m=>m.periods.map(p=>p.period_start)))].sort();
 const fmt = (v: unknown) => typeof v === 'number' ? rli(v) : '—';
 return <section className="sr-block">
 <h3>Beban anggota per minggu</h3>
 {runs ? <div className="sr-switch" role="group" aria-label="Metrik beban">{(['load','median','risk'] as const).map((key,i)=><button key={key} type="button" aria-pressed={active===key} onClick={()=>setMetric(key)}>{['RLI simulasi','Perkiraan RLI (P50)','Risiko overload'][i]}</button>)}</div> : null}
 <p className="sr-muted">{active==='risk'?'Persentase percobaan Monte Carlo dengan Resource Load Index minggu tersebut di atas threshold (Overload Flag).':active==='median'?'Perkiraan tengah (P50) Resource Load Index skenario dari percobaan Monte Carlo. Warna menunjukkan tingkat beban, bukan status overload.':'Resource Load Index (RLI) mingguan skenario. Merah = RLI di atas threshold (Overload Flag).'} Klik nama anggota untuk detail. {runs ? 'Klik sel untuk melihat statistik periode.' : null}</p>
 {<div className="sr-legend">{(active==='risk'?[['missing','Tidak ada data'],['idle','0%'],['risk-low','> 0–< 25%'],['risk-mid','25–< 50%'],['risk-high','≥ 50%']]:[['missing','Tidak ada data'],['idle','0'],['low','< 0.50'],['medium','0.50–< 0.90'],['high','≥ 0.90'],...(active==='load'?[['overload','Overload (RLI > threshold)']]:[])]).map(([key,label])=><span key={key}><i className={`sr-heat ${key}`}/>{label}</span>)}</div>}
 <div className={`sr-drawer-layout ${detail ? 'is-open' : ''}`}><div className="sr-scroll"><table className="data-table sr-table"><thead><tr><th>Anggota</th>{weeks.map(w=><th key={w}>{w.slice(5)}</th>)}</tr></thead><tbody>{members.map(m=><tr key={m.resource_id}><td><button type="button" className="sr-link" onClick={()=>onSelect(m)}>{m.resource_name}</button></td>{weeks.map(w=>{
 const p=m.periods.find(x=>x.period_start===w); const r=p?.replication;
 const risk=r && runs ? r.scenario_overloaded_runs/runs : null;
 const median=typeof r?.scenario.median==='number'?r.scenario.median:null;
 const value=active==='risk'?risk:active==='median'?median:p?.scenario_rli;
 const color=active==='risk'?(risk==null?'missing':risk===0?'idle':risk<.25?'risk-low':risk<.5?'risk-mid':'risk-high'):heatClass(value,active==='load'&&p?.overloaded);
 return <td key={w} className={`sr-heat ${color}`}>{r && runs ? <button type="button" className="sr-cell-trigger" aria-label={`${m.resource_name}, ${w}, ${active==='risk'?percent(value):rli(value)}, lihat detail`} aria-pressed={selection?.id===m.resource_id && selection?.week===w} onClick={()=>setSelection({id:m.resource_id,week:w})}>{active==='risk'?percent(value):rli(value)}{active==='load'&&p?.overloaded?' !':''}</button> : <>{active==='risk'?percent(value):rli(value)}{active==='load'&&p?.overloaded?' !':''}</>}</td>;
 })}</tr>)}</tbody></table></div>{detail && period && member && runs && <StatisticsDrawer title="Detail beban anggota" onClose={()=>setSelection(null)}><strong>{member.resource_name}</strong><p className="sr-muted">{formatValue(period.period_start)} – {formatValue(period.period_end)} · {runs} percobaan Monte Carlo</p><section><RangeComparison baseline={detail.baseline} scenario={detail.scenario} format={fmt} extra={[["Simulasi utama",rli(period.baseline_rli),rli(period.scenario_rli)],["Risiko overload",`${percent(detail.baseline_overloaded_runs/runs)} (${detail.baseline_overloaded_runs}/${runs})`,`${percent(detail.scenario_overloaded_runs/runs)} (${detail.scenario_overloaded_runs}/${runs})`]]}/></section></StatisticsDrawer>}</div></section>;
}
