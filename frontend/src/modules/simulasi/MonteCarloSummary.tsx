import type { SimulationResult } from './simulationTypes';
import { ReplicationRange } from './ReplicationRange';
export function MonteCarloSummary({run}: {run: SimulationResult}) {
 if (!run.replication) return null;
 return <section className="sr-block"><h3>Hasil Monte Carlo</h3><p className="sr-muted">{run.replication.runs} replikasi · Seed {run.replication.seed}. Median adalah P50; P10–P90 menunjukkan rentang persentil, bukan jaminan hasil. Tabel dan jadwal utama tetap menampilkan hasil simulasi utama.</p><div className="sr-scroll"><table className="data-table sr-table"><thead><tr><th>Proyek</th><th>Selesai baseline</th><th>Selesai skenario</th><th>Schedule Variance baseline (hari kerja)</th><th>Schedule Variance skenario (hari kerja)</th></tr></thead><tbody>{run.projects.map(p=><tr key={p.project_id}><td>{p.project_name}</td><td><ReplicationRange range={p.replication?.baseline_completion}/></td><td><ReplicationRange range={p.replication?.scenario_completion}/></td><td><ReplicationRange range={p.replication?.baseline_sv}/></td><td><ReplicationRange range={p.replication?.scenario_sv}/></td></tr>)}</tbody></table></div></section>;
}
