import { useState } from 'react';
import { Button, Modal } from '../../shared/ui';
import type { SimulationResult, ProjectResult, MemberResult, TaskResult } from './simulationTypes';
import { getProjectDrilldown, getMemberDrilldown } from './simulationApi';
import { SimulationGantt } from './SimulationGantt';
import { MemberLoadHeatmap, percent } from './MemberLoadHeatmap';
import { RangeComparison, formatValue } from './ReplicationRange';
import './simulationResult.css';
const date = (v: string | null) => v ? new Intl.DateTimeFormat("id-ID", {day:"numeric",month:"short",year:"numeric",timeZone:"UTC"}).format(new Date(v+"T00:00:00Z")) : "—";
type Detail = {title:string; tasks:TaskResult[]; member?:MemberResult; project?:ProjectResult};
export function SimulationResultView({run}:{run:SimulationResult}) {
 const [detail,setDetail]=useState<Detail|null>(null);
 const [loading,setLoading]=useState(false);
 const [error,setError]=useState('');
 const [projectMode,setProjectMode]=useState<'summary'|'risk'>('summary');
 const risk=!!run.replication && projectMode==='risk';
 const [affectedOnly,setAffectedOnly]=useState(false);
 async function project(p:ProjectResult) {
  setLoading(true);setError('');setDetail({title:p.project_name,project:p,tasks:[]});
  try { const data=run.saved?await getProjectDrilldown(run.scenario.scenario_id,p.project_id):null;
   if(data && data.run_id!==run.run_id) throw new Error('Hasil tersimpan telah berubah. Muat ulang halaman.');
   setDetail({title:p.project_name,project:p,tasks:data?.tasks??run.tasks.filter(t=>t.project_id===p.project_id)});
  } catch(e){setError(e instanceof Error?e.message:'Gagal memuat detail.');} finally{setLoading(false);}
 }
 async function member(m:MemberResult) {
  setLoading(true);setError('');setDetail({title:m.resource_name,tasks:[],member:m});
  try {const data=run.saved?await getMemberDrilldown(run.scenario.scenario_id,m.resource_id):null;
   if(data && data.run_id!==run.run_id) throw new Error('Hasil tersimpan telah berubah. Muat ulang halaman.');
   setDetail({title:m.resource_name,member:data?.member??m,tasks:data?.tasks??run.tasks.filter(t=>t.baseline?.owner===m.resource_id||t.scenario?.owner===m.resource_id||m.days.some(d=>d.scenario_tasks.includes(t.task_id)))});
  } catch(e){setError(e instanceof Error?e.message:'Gagal memuat detail.');} finally{setLoading(false);}
 }
 const metrics=[['Proyek terdampak',run.summary.affected_projects],['Task terdampak',run.summary.affected_tasks],['Anggota terdampak',run.summary.affected_members],['Task tidak terjadwal',run.summary.unscheduled_tasks],['Anggota overload',run.summary.overloaded_members]] as const;
 return <div className="sr-result"><div className="sr-metrics">{metrics.map(([label,value])=><article className="sr-block" key={label}><span className="sr-muted">{label}</span><strong>{value}</strong></article>)}</div>
 <section className="sr-block"><div className="sr-heading"><h3>Dampak terhadap proyek</h3><label><input type="checkbox" checked={affectedOnly} onChange={e=>setAffectedOnly(e.target.checked)}/> Hanya terdampak</label></div>{run.replication && <><div className="sr-switch" role="group" aria-label="Tampilan proyek"><button type="button" aria-pressed={!risk} onClick={()=>setProjectMode('summary')}>Ringkasan dampak</button><button type="button" aria-pressed={risk} onClick={()=>setProjectMode('risk')}>Analisis risiko (Monte Carlo)</button></div>{risk && <p className="sr-muted">Berdasarkan {run.replication.runs} percobaan Monte Carlo. Rentang P10–P90 menggambarkan sekitar 80% bagian tengah hasil, bukan jaminan.</p>}</>}<div className="sr-scroll"><table className="data-table sr-table"><thead><tr><th>Proyek</th>{risk ? <><th>Perkiraan selesai awal (P50)</th><th>Perkiraan selesai skenario (P50)</th><th>Rentang selesai skenario (P10–P90)</th></> : <><th>Pergeseran (hari kerja)</th><th>Task terdampak</th><th>Status target</th></>}</tr></thead><tbody>{run.projects.filter(p=>!affectedOnly||p.affected).map(p=><tr key={p.project_id}><td><button className="sr-link" onClick={()=>void project(p)}>{p.project_name} <span aria-hidden="true">›</span></button>{p.affected&&<small> · Terdampak</small>}</td>{risk ? <><td>{formatValue(p.replication?.baseline_completion.median)}</td><td>{formatValue(p.replication?.scenario_completion.median)}</td><td>{p.replication ? `${formatValue(p.replication.scenario_completion.p10)} – ${formatValue(p.replication.scenario_completion.p90)}` : '—'}</td></> : <><td className={p.completion_shift && p.completion_shift > 0 ? 'sr-late' : ''}>{p.completion_shift == null ? '—' : `${p.completion_shift > 0 ? '+' : ''}${p.completion_shift}`}</td><td>{p.affected_task_count}</td><td><span className={`sr-status ${p.scenario.on_target === false ? "late" : p.scenario.on_target === true ? "good" : "neutral"}`}>{p.scenario.on_target === true ? "Tercapai" : p.scenario.on_target === false ? "Terlewat" : "Belum diketahui"}</span></td></>}</tr>)}</tbody></table></div></section>

 <SimulationGantt tasks={run.tasks} projects={run.projects}/><MemberLoadHeatmap members={run.members} runs={run.replication?.runs} onSelect={m=>void member(m)}/>
 <section className="sr-block"><h3>Peringatan & catatan</h3>{!run.warnings.length&&!run.notes.length?<p>Tidak ada peringatan atau catatan.</p>:<ul>{run.warnings.map((w,i)=><li key={i}>{w.run}: {w.message}</li>)}{run.notes.map((n,i)=><li key={`n${i}`}>{n}</li>)}</ul>}</section>
 <Modal open={detail!==null} onClose={()=>{if(!loading)setDetail(null)}} title={detail?.title??'Detail'} size="lg" footer={<Button variant="outline" disabled={loading} onClick={()=>setDetail(null)}>Tutup</Button>}>
 {loading?<p role="status">Memuat detail...</p>:error?<p role="alert">{error}</p>:detail&&<>{detail.project && <section className="sr-block"><h3>Jadwal proyek</h3><p>Target: {date(detail.project.scenario.target_launch_date)} · Kondisi awal: {date(detail.project.baseline.completion)} · Skenario: {date(detail.project.scenario.completion)}</p>{detail.project.replication && <><h4>Perkiraan selesai (Monte Carlo)</h4><RangeComparison baseline={detail.project.replication.baseline_completion} scenario={detail.project.replication.scenario_completion}/><h4>Keterlambatan (hari)</h4><RangeComparison baseline={detail.project.replication.baseline_delay} scenario={detail.project.replication.scenario_delay}/></>}</section>}{detail.member&&<div className="sr-scroll sr-member-detail"><table className="data-table sr-table"><thead><tr><th>Tanggal</th><th>Baseline</th><th>Skenario</th><th>Kapasitas (RLI)</th><th>Task</th></tr></thead><tbody>{detail.member.days.map(d=><tr key={d.day}><td>{d.day}</td><td>{percent(d.baseline_load)}</td><td className={d.overloaded?'sr-heat overload':''}>{percent(d.scenario_load)}</td><td>{d.scenario_capacity.toFixed(1)}</td><td>{d.scenario_tasks.join(', ')||'—'}</td></tr>)}</tbody></table></div>}{detail.member && run.replication && <MemberLoadHeatmap members={[detail.member]} runs={run.replication.runs} onSelect={()=>{}}/>}<SimulationGantt tasks={detail.tasks}/></>}
 </Modal></div>;
}
