import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  createScenario,
  deleteScenario,
  duplicateScenario,
  getBaseline,
  getBaselineById,
  getScenario,
  listScenarios,
  listSituations,
  saveScenario,
  updateScenario,
} from './api';
import type {
  Baseline,
  Scenario,
  ScenarioSummary,
  SituationInfo,
} from './types';
import { ApiError } from '../../shared/api/client';
import { Button, ConfirmDialog } from '../../shared/ui';
import { ScenarioForm, type Draft } from './ScenarioForm';
import {
  createInitialParameters,
  serializeParameters,
  SITUATION_ORDER,
} from './situations';
import {
  mapParameterErrors,
  mapApiErrors,
  type FieldErrors,
} from './validation';
import './simulasi.css';
function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : 'Terjadi kesalahan. Silakan coba kembali.';
}
function toDraft(scenario: Scenario): Draft {
  return {
    name: scenario.name,
    parameters: structuredClone(scenario.parameters),
  };
}
function sourceLabel(source: string): string {
  return source === 'synthetic' ? 'Sintetis' : 'PuTI';
}
export function SimulasiPage() {
  const navigate = useNavigate();
  const { scenarioId } = useParams();
  const [confirmation, setConfirmation] = useState<null | { kind: 'delete' } | { kind: 'leave'; action: () => void }>(null);
  const [baseline, setBaseline] = useState<Baseline | null>(null);
  const [scenarioBaseline, setScenarioBaseline] =
    useState<Baseline | null>(null);
  const [situations, setSituations] = useState<SituationInfo[]>([]);
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [selected, setSelected] = useState<Scenario | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [dirty, setDirty] = useState(false);
  const [showSituations, setShowSituations] = useState(true);
  const [search, setSearch] = useState('');
  const [reloadKey, setReloadKey] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setLoading(true);
      setLoadError(null);
      try {
        const [current, definitions, list] = await Promise.all([
          getBaseline(controller.signal).catch((err: unknown) => {
            if (
err instanceof ApiError &&
              (err.status === 404 || err.status === 409)
            ) {
              return null;
            }
            throw err;
          }),
          listSituations(controller.signal),
          listScenarios({}, controller.signal),
        ]);
        if (controller.signal.aborted) return;
        setBaseline(current);
        setSituations(definitions);
        setScenarios(list);
      } catch (err) {
        if (!controller.signal.aborted) {
          setLoadError(errorMessage(err));
        }
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      }
    }
    void load();
    return () => controller.abort();
  }, [reloadKey]);
  const activeSituation = situations.find(
    (item) => item.situation === selected?.situation,
  );
  const displayedBaseline = scenarioBaseline ?? baseline;
  const orderedSituations = [...situations].sort(
    (a, b) =>
      SITUATION_ORDER.indexOf(a.situation) -
      SITUATION_ORDER.indexOf(b.situation),
  );
  const filteredScenarios = scenarios.filter((scenario) => {
    const query = search.trim().toLowerCase();
    const label = situations.find(
      (item) => item.situation === scenario.situation,
    )?.label ?? scenario.situation;
    return `${scenario.name} ${label} ${scenario.status}`
      .toLowerCase()
      .includes(query);
  });
  function syncScenario(scenario: Scenario) {
    setScenarios((previous) => [
scenario,
      ...previous.filter(
        (item) => item.scenario_id !== scenario.scenario_id,
      ),
    ]);
    setSelected(scenario);
    setDraft(toDraft(scenario));
    setDirty(false);
  }
  function confirmLeave(action: () => void) {
    if (dirty) setConfirmation({ kind: 'leave', action });
    else action();
  }

  useEffect(() => {
    if (loading || loadError || busy) return;
    if (!scenarioId) {
      if (selected) {
        setSelected(null);
        setScenarioBaseline(null);
        setDraft(null);
        setDirty(false);
        setErrors({});
        setShowSituations(true);
      }
      return;
    }
    const id = Number(scenarioId);
    if (!/^\d+$/.test(scenarioId) || !Number.isSafeInteger(id) || id < 1) {
      navigate('/simulasi', { replace: true });
      return;
    }
    if (selected?.scenario_id === id) return;
    const controller = new AbortController();
    setShowSituations(false);
    setError(null);
    setNotice(null);
    setSelected(null);
    setDraft(null);
    setScenarioBaseline(null);
    setDirty(false);
    setErrors({});
    void (async () => {
      try {
        const scenario = await getScenario(id, controller.signal);
        const snapshot = baseline?.snapshot_id === scenario.snapshot_id
          ? baseline : await getBaselineById(scenario.snapshot_id, controller.signal);
        if (controller.signal.aborted) return;
        syncScenario(scenario);
        setScenarioBaseline(snapshot);
        setErrors(mapParameterErrors(scenario.parameter_errors));
      } catch (err) {
        if (controller.signal.aborted) return;
        setError(errorMessage(err));
        setShowSituations(true);
        navigate('/simulasi', { replace: true });
      }
    })();
    return () => controller.abort();
  }, [scenarioId, loading, loadError, busy, baseline, selected?.scenario_id, navigate]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  async function act(
action: string,
    callback: () => Promise<void>,
  ) {
    setBusy(action);
    setError(null);
    setNotice(null);
    try {
      await callback();
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        setErrors(mapApiErrors(err.issues));
      }
      setError(errorMessage(err));
      setConfirmation(null);
    } finally {
      setBusy(null);
    }
  }
  function startNew() {
    confirmLeave(() => {
      setError(null);
      setNotice(null);
      navigate('/simulasi');
    });
  }
  async function handleCreate(situation: SituationInfo) {
    if (!baseline || busy) return;
    await act('create', async () => {
      const parameters = createInitialParameters(
situation.parameters_schema,
      );
      const scenario = await createScenario({
        name: `Skenario ${situation.label}`,
        situation: situation.situation,
        snapshot_id: baseline.snapshot_id,
        parameters: serializeParameters(parameters),
      });
      syncScenario(scenario);
      setDraft({
        name: scenario.name,
        parameters: {
          ...parameters,
          ...scenario.parameters,
        },
      });
      setScenarioBaseline(baseline);
      setErrors({});
      setShowSituations(false);
      setNotice('Draft skenario berhasil dibuat.');
      navigate(`/simulasi/${scenario.scenario_id}`);
    });
  }
  function handleSelect(id: number) {
    if (busy || selected?.scenario_id === id) return;
    confirmLeave(() => navigate(`/simulasi/${id}`));
  }
  function handleDraftChange(next: Draft) {
    setDraft(next);
    setDirty(true);
    setErrors({});
    setNotice(null);
  }
  async function handleSave() {
    if (!selected || !draft || busy) return;
    if (!draft.name.trim()) {
      setErrors({
        name: ['Nama skenario wajib diisi.'],
      });
      return;
    }
    await act('save', async () => {
      setErrors({});
      const updated = await updateScenario(
        selected.scenario_id,
        {
          name: draft.name.trim(),
          parameters: serializeParameters(draft.parameters),
        },
      );
      syncScenario(updated);
      if (updated.parameter_errors.length > 0) {
        setErrors(
          mapParameterErrors(updated.parameter_errors),
        );
        setError(
          'Periksa kembali parameter yang belum valid.',
        );
        return;
      }
      const saved = await saveScenario(updated.scenario_id);
      syncScenario(saved);
      setErrors({});
      setNotice('Skenario berhasil disimpan.');
    });
  }
  async function handleDuplicate() {
    if (!selected || busy) return;
    confirmLeave(() => { void act('duplicate', async () => {
      const copy = await duplicateScenario(
        selected.scenario_id,
      );
      syncScenario(copy);
      setErrors({});
      setNotice('Skenario berhasil diduplikat.');
      navigate(`/simulasi/${copy.scenario_id}`);
    }); });
  }

  async function handleDelete() {
    if (!selected || busy) return;

    await act('delete', async () => {
      await deleteScenario(selected.scenario_id);
      setScenarios((previous) =>
  previous.filter(
          (item) => item.scenario_id !== selected.scenario_id,
        ),
      );
      setSelected(null);
      setScenarioBaseline(null);
      setDraft(null);
      setErrors({});
      setDirty(false);
      setShowSituations(true);
      setNotice('Skenario berhasil dihapus.');
      setConfirmation(null);
      navigate('/simulasi', { replace: true });
    });
  }

  if (loading) {
    return (
      <section className="module-panel">
        <div className="empty-state">
          <p>Memuat data skenario...</p>
        </div>
      </section>
    );
  }
  if (loadError) {
    return (
      <section className="module-panel">
        <div className="empty-state">
          <h3>Data tidak dapat dimuat</h3>
          <p>{loadError}</p>
          <Button
variant="outline"
onClick={() => setReloadKey((key) => key + 1)}
          >
            Coba lagi
          </Button>
        </div>
      </section>
    );
  }
  return (
    <div className="sim-page">
      {displayedBaseline ? (
        <div className="sim-context">
          <span className="badge badge-accent">
            Sumber data: {sourceLabel(displayedBaseline.data_source)}
          </span>
          <span className="badge">
            Snapshot #{displayedBaseline.snapshot_id}
          </span>
          <span className="badge">
            {displayedBaseline.projects.length} proyek
            {' · '}
            {displayedBaseline.resources.length} anggota
            {' · '}
            {displayedBaseline.tasks.length} task
          </span>
        </div>
      ) : (
        <div className="notice notice-danger" role="alert">
          Baseline snapshot belum tersedia.
          Skenario baru belum dapat dibuat.
        </div>
      )}
      {notice && (
        <div className="sim-success-notice" role="status">
          {notice}
        </div>
      )}
      {error && (
        <div className="notice notice-danger" role="alert">
          {error}
        </div>
      )}
      <div className="sim-layout">
        <aside
className="module-panel sim-list"
aria-label="Daftar skenario"
        >
          <div className="panel-heading">
            <h2>Skenario</h2>
            <Button
variant="outline"
size="sm"
disabled={!baseline || busy !== null}
onClick={startNew}
            >
              + Baru
            </Button>
          </div>
          <div className="sim-list-search">
            <input
type="search"
placeholder="Cari skenario..."
aria-label="Cari skenario"
value={search}
onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <ul>
            {showSituations && (
              <li>
                <button
type="button"
className="sim-list-item active"
disabled={busy !== null}
onClick={startNew}
                >
                  <strong>Skenario baru</strong>
                  <small>Pilih situasi keputusan</small>
                </button>
              </li>
            )}
            {filteredScenarios.map((scenario) => (
              <li key={scenario.scenario_id}>
                <button
type="button"
disabled={busy !== null}
className={`sim-list-item ${
                    selected?.scenario_id === scenario.scenario_id
                      ? 'active'
                      : ''
                  }`}
onClick={() =>
                    void handleSelect(scenario.scenario_id)
                  }
                >
                  <strong>{scenario.name}</strong>
                  <small>
                    {situations.find(
                      (item) => item.situation === scenario.situation,
                    )?.label ?? scenario.situation}
                  </small>
                  <span
className={`sim-status sim-status-${scenario.status}`}
                  >
                    {scenario.status === 'saved'
                      ? 'Tersimpan'
                      : 'Draft'}
                  </span>
                </button>
              </li>
            ))}
            {filteredScenarios.length === 0 && (
              <li className="sim-list-empty">
                {search
                  ? 'Skenario tidak ditemukan.'
                  : 'Belum ada skenario tersimpan.'}
              </li>
            )}
          </ul>
        </aside>
        {showSituations ? (
          <section
className="module-panel"
aria-label="Pilih situasi keputusan"
          >
            <div className="panel-heading">
              <div>
                <h2>Buat Skenario Baru</h2>
                <p className="sim-editor-subtitle">
                  Pilih situasi keputusan yang ingin disimulasikan.
                </p>
              </div>
            </div>
            <div className="sim-situation-grid">
              {orderedSituations.map((situation, index) => (
                <button
type="button"
className="sim-situation-card"
key={situation.situation}
disabled={!baseline || busy !== null}
onClick={() => void handleCreate(situation)}
                >
                  <span className="sim-situation-number">
                    {String(index + 1).padStart(2, '0')}
                  </span>
                  <strong>{situation.label}</strong>
                  <small>{situation.description}</small>
                </button>
              ))}
            </div>
            {busy === 'create' && (
              <p className="sim-editor-subtitle" role="status">
                Membuat draft skenario...
              </p>
            )}
          </section>
        ) : selected && draft && activeSituation && scenarioBaseline ? (
          <section
className="module-panel"
aria-label="Scenario setup"
          >
            <div className="panel-heading sim-editor-heading">
              <div className="sim-editor-heading-content">
                <h2>
                  {draft.name.trim() || 'Skenario tanpa nama'}
                </h2>
                <p className="sim-editor-subtitle">
                  {activeSituation.label}
                  {dirty && ' · Perubahan belum disimpan'}
                </p>
              </div>
              <span
className={`sim-status sim-status-${
                  dirty ? 'draft' : selected.status
                }`}
              >
                {!dirty && selected.status === 'saved'
                  ? 'Tersimpan'
                  : 'Draft'}
              </span>
            </div>
            <form
className="sim-editor"
noValidate
onSubmit={(e) => {
e.preventDefault();
                void handleSave();
              }}
            >
              <ScenarioForm
baseline={scenarioBaseline}
situation={activeSituation}
draft={draft}
errors={errors}
disabled={busy !== null}
onChange={handleDraftChange}
              />
              <div className="sim-actions">
                <div className="sim-actions-secondary">
                  <Button
type="button"
variant="outline"
disabled={busy !== null}
loading={busy === 'duplicate'}
onClick={() => void handleDuplicate()}
                  >
                    Duplikat
                  </Button>
                  <Button type="button" variant="danger" disabled={busy !== null} loading={busy === 'delete'} onClick={() => setConfirmation({ kind: 'delete' })}>
                    Hapus
                  </Button>
                </div>
                <div className="sim-actions-primary">
                  <Button
type="submit"
variant="primary"
disabled={busy !== null}
loading={busy === 'save'}
                  >
                    {busy === 'save'
                      ? 'Menyimpan...'
                      : 'Simpan Skenario'}
                  </Button>
                </div>
              </div>
            </form>
          </section>
        ) : (
          <section className="module-panel">
            <div className="empty-state">
              <p>Memuat konfigurasi skenario...</p>
            </div>
          </section>
        )}
      </div>
      <ConfirmDialog
        open={confirmation !== null}
        title={confirmation?.kind === 'delete' ? 'Hapus skenario?' : 'Tinggalkan perubahan?'}
        description={confirmation?.kind === 'delete'
          ? `Skenario "${selected?.name ?? ''}" akan dihapus permanen.`
          : 'Perubahan lokal yang belum disimpan akan hilang.'}
        confirmLabel={confirmation?.kind === 'delete' ? 'Hapus' : 'Lanjutkan'}
        cancelLabel="Batal"
        destructive
        loading={busy === 'delete'}
        onClose={() => { if (!busy) setConfirmation(null); }}
        onConfirm={() => {
          if (busy) return;
          if (confirmation?.kind === 'delete') {
            void handleDelete();
          } else if (confirmation?.kind === 'leave') {
            const action = confirmation.action;
            setConfirmation(null);
            action();
          }
        }}
      />
    </div>
  );
}
