import { useEffect, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { Button } from '../../shared/ui';
import { getSimulationResult } from './simulationApi';
import type { SimulationResult } from './simulationTypes';
import type { Draft } from './ScenarioForm';
import { SimulationResultView } from './SimulationResultView';

export function SimulationResultPage() {
  const { scenarioId } = useParams();
  const id = Number(scenarioId);
  const location = useLocation();
  const navigate = useNavigate();
  const state = location.state as { result?: SimulationResult; draft?: Draft } | null;
  const initial = state?.result;
  const [run, setRun] = useState<SimulationResult | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const validId = Number.isSafeInteger(id) && id > 0;

  useEffect(() => {
    const controller = new AbortController();
    setError(''); setRun(null); setLoading(true);
    if (!validId) {
      setError('ID skenario tidak valid.'); setLoading(false); return;
    }
    if (initial?.scenario.scenario_id === id) {
      setRun(initial); setLoading(false); return;
    }
    getSimulationResult(id, controller.signal)
      .then(r => { if (!controller.signal.aborted) setRun(r); })
      .catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Hasil tidak tersedia.'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [id, initial, validId]);

  function backToSetup(rerun = false) {
    navigate(validId ? `/simulasi/${id}` : '/simulasi', {
      state: { scenarioId: id, draft: initial?.scenario.scenario_id === id ? state?.draft : undefined, rerun },
    });
  }

  return <div className="sr-page">
    <div className="sr-heading">
      <div>
        <p className="sr-muted">SIMULASI / HASIL SIMULASI</p>
        <h1>Hasil Simulasi</h1>
        {run && <><h2>{run.scenario.name}</h2>
          <p className="sr-muted">{run.data_source === 'synthetic' ? 'Data Sintetis' : 'Data PuTI'} · Snapshot #{run.snapshot_id} · {run.start_date} – {run.end_date} · {run.saved ? 'Tersimpan' : 'Sementara'} · {run.duration_ms} ms · {run.replication ? `Monte Carlo (${run.replication.runs} replikasi, seed ${run.replication.seed})` : 'Single Simulation'}</p></>}
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <Button variant="outline" onClick={() => backToSetup()}>Edit Skenario</Button>
        <Button variant="primary" disabled={!validId || loading} onClick={() => backToSetup(true)}>Jalankan Ulang</Button>
      </div>
    </div>
    {loading && <p role="status">Memuat hasil simulasi...</p>}
    {error && <p role="alert">{error} — Jalankan simulasi dari halaman skenario.</p>}
    {run && <>
      {run.is_stale && <p role="alert" className="notice notice-warning">Parameter skenario telah berubah. Jalankan ulang untuk memperbarui hasil.</p>}
      {!run.saved && <p className="sr-muted">Hasil sementara tidak tersimpan di server.</p>}
      {state?.draft && <p className="sr-muted">Perubahan lokal akan dikembalikan saat mengedit skenario.</p>}
      <SimulationResultView key={`${id}-${run.run_id ?? 'temporary'}`} run={run}/>
    </>}
  </div>;
}
