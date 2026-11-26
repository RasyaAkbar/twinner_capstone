import { useEffect, useState } from 'react';
import { Button, Modal } from '../../shared/ui';
import type {
  RunMode,
  RunPreview,
  SimulationRunRequest,
} from './simulationTypes';

import './simulationRun.css';

interface Props {
  open: boolean;
  preview: RunPreview | null;
  scenarioName: string;
  dirty: boolean;
  loading: boolean;
  running: boolean;
  error: string | null;
  onClose: () => void;
  onRun: (input: SimulationRunRequest) => void;
}

export function SimulationRunDialog({
  open,
  preview,
  scenarioName,
  dirty,
  loading,
  running,
  error,
  onClose,
  onRun,
}: Props) {
  const [mode, setMode] = useState<RunMode>('run_only');
  const [startDate, setStartDate] = useState('');
  const [monteCarlo, setMonteCarlo] = useState(false);
  const [runs, setRuns] = useState('500');
  const [seed, setSeed] = useState('42');

  useEffect(() => {
    if (!open) return;

    setMode('run_only');
    setStartDate('');
    setMonteCarlo(false);
    setRuns('500');
    setSeed('42');
  }, [open]);

  useEffect(() => {
    if (!open || !preview) return;

    setStartDate(preview.default_start_date);
    setRuns(String(preview.default_replications));
  }, [open, preview]);

  const runsValid =
    runs.trim() !== '' &&
    Number.isInteger(Number(runs)) &&
    Number(runs) >= 10 &&
    Number(runs) <= 2000;

  const seedValid =
    seed.trim() !== '' &&
    Number.isInteger(Number(seed)) &&
    Number(seed) >= 0 &&
    Number(seed) <= 2147483647;

  const configValid =
    !monteCarlo || (runsValid && seedValid);

  const canRun =
    preview !== null &&
    (preview.can_run || dirty) &&
    configValid &&
    !loading &&
    !running;

  function handleClose() {
    if (!running) onClose();
  }

  function handleRun() {
    if (!canRun) return;

    const input: SimulationRunRequest = {
      mode,
      start_date: startDate || null,
      replication: monteCarlo
        ? {
            runs: Number(runs),
            seed: Number(seed),
          }
        : null,
    };

    onRun(input);
  }

  return (
    <Modal
      open={open}
      onClose={handleClose}
      title="Jalankan Simulasi"
      description="Konfigurasikan simulasi what-if untuk skenario yang dipilih."
      size="md"
      footer={
        <>
          <Button
            variant="outline"
            disabled={running}
            onClick={handleClose}
          >
            Batal
          </Button>

          <Button
            variant="primary"
            disabled={!canRun}
            loading={running}
            onClick={handleRun}
          >
            {running
              ? 'Menjalankan simulasi...'
              : 'Jalankan Simulasi'}
          </Button>
        </>
      }
    >
      <div className="sim-run-dialog" aria-busy={running || loading}>
        {loading && (
          <div className="sim-run-loading" role="status">
            <span className="sim-run-spinner" />
            <p>Memuat konfigurasi simulasi...</p>
          </div>
        )}

        {error && (
          <div className="notice notice-danger" role="alert">
            {error}
          </div>
        )}

        {!loading && preview && (
          <>
            <div className="sim-run-context">
              <span className="sim-run-caption">
                Skenario
              </span>

              <strong>{scenarioName}</strong>

              <div className="sim-run-context-meta">
                <span className="badge badge-accent">
                  {preview.data_source === 'synthetic'
                    ? 'Data Sintetis'
                    : 'Data PuTI'}
                </span>

                <span className="badge">
                  Snapshot #{preview.snapshot_id}
                </span>
              </div>
            </div>

            {!preview.can_run && !dirty && (
              <div
                className="notice notice-danger"
                role="alert"
              >
                Parameter skenario belum lengkap atau
                belum valid. Lengkapi parameter sebelum
                menjalankan simulasi.
              </div>
            )}

            {dirty && (
              <div className="sim-run-info">
                Simulasi akan menggunakan parameter
                terbaru dari formulir. Parameter akan
                divalidasi oleh server saat simulasi
                dijalankan.
              </div>
            )}

            <fieldset
              className="sim-run-fieldset"
              disabled={running}
            >
              <legend>Mode simulasi</legend>

              <label
                className={`sim-run-option ${
                  mode === 'run_only' ? 'selected' : ''
                }`}
              >
                <input
                  type="radio"
                  name="simulation-mode"
                  value="run_only"
                  checked={mode === 'run_only'}
                  onChange={() => setMode('run_only')}
                />

                <div>
                  <strong>Run Only</strong>
                  <small>
                    Jalankan simulasi tanpa menyimpan
                    hasil atau perubahan skenario.
                  </small>
                </div>
              </label>

              <label
                className={`sim-run-option ${
                  mode === 'run_and_save'
                    ? 'selected'
                    : ''
                }`}
              >
                <input
                  type="radio"
                  name="simulation-mode"
                  value="run_and_save"
                  checked={mode === 'run_and_save'}
                  onChange={() =>
                    setMode('run_and_save')
                  }
                />

                <div>
                  <strong>Run and Save</strong>
                  <small>
                    Simpan parameter skenario dan hasil
                    simulasi agar dapat dibuka kembali.
                  </small>
                </div>
              </label>
            </fieldset>

            <label className="sim-run-field">
              <span>Tanggal mulai simulasi</span>

              <input
                type="date"
                value={startDate}
                disabled={running}
                onChange={(event) =>
                  setStartDate(event.target.value)
                }
              />

              <small>
                Default mengikuti tanggal mulai dari
                baseline snapshot.
              </small>
            </label>

            <div className="sim-run-divider" />

            <label className="sim-run-checkbox">
              <input
                type="checkbox"
                checked={monteCarlo}
                disabled={running}
                onChange={(event) =>
                  setMonteCarlo(event.target.checked)
                }
              />

              <div>
                <strong>
                  Aktifkan replikasi Monte Carlo
                </strong>

                <small>
                  Jalankan beberapa replikasi untuk
                  mendapatkan median dan rentang hasil.
                  Proses dapat memerlukan waktu lebih lama.
                </small>
              </div>
            </label>

            {monteCarlo && (
              <div className="sim-run-config-grid">
                <label className="sim-run-field">
                  <span>Jumlah replikasi</span>

                  <input
                    type="number"
                    min={10}
                    max={2000}
                    step={1}
                    value={runs}
                    disabled={running}
                    aria-invalid={!runsValid}
                    onChange={(event) =>
                      setRuns(event.target.value)
                    }
                  />

                  <small>
                    Minimum 10, maksimum 2.000.
                  </small>

                  {!runsValid && (
                    <small className="sim-run-field-error">
                      Masukkan bilangan bulat 10–2.000.
                    </small>
                  )}
                </label>

                <label className="sim-run-field">
                  <span>Random seed</span>

                  <input
                    type="number"
                    min={0}
                    max={2147483647}
                    step={1}
                    value={seed}
                    disabled={running}
                    aria-invalid={!seedValid}
                    onChange={(event) =>
                      setSeed(event.target.value)
                    }
                  />

                  <small>
                    Seed yang sama memungkinkan hasil
                    direproduksi dengan input yang sama.
                  </small>

                  {!seedValid && (
                    <small className="sim-run-field-error">
                      Seed harus berupa bilangan bulat
                      antara 0 dan 2147483647.
                    </small>
                  )}
                </label>
              </div>
            )}

            {preview.latest_run &&
              mode === 'run_and_save' && (
                <div className="sim-run-info">
                  Hasil simulasi terbaru akan menjadi
                  hasil yang ditampilkan ketika skenario
                  dibuka kembali.
                </div>
              )}

            {running && (
              <div
                className="sim-run-loading sim-run-progress"
                role="status"
                aria-live="polite"
              >
                <span className="sim-run-spinner" />

                <div>
                  <strong>
                    Simulasi sedang berjalan
                  </strong>

                  <p>
                    Sistem sedang menghitung jadwal,
                    kapasitas, workload, dan dampak
                    skenario terhadap baseline.
                  </p>

                  <small>
                    Mohon tunggu hingga proses selesai.
                  </small>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}
