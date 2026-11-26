import { useState } from 'react';
import { ModulePlaceholder } from '../../shared/components/ModulePlaceholder';

const MODES = {
  global: {
    label: 'Global (baseline)',
    title: 'Rekomendasi global',
    description: 'Rekomendasi aksi untuk kondisi baseline saat ini, tanpa skenario, akan tersedia saat modul DSS siap digunakan.',
    areas: ['Diagnosis masalah', 'Penugasan dan pengganti', 'Penambahan hari', 'Alasan rekomendasi'],
  },
  scenario: {
    label: 'Per skenario',
    title: 'Rekomendasi per skenario',
    description: 'Rekomendasi respons untuk skenario what-if yang sudah disimulasikan akan tersedia saat modul DSS siap digunakan.',
    areas: ['Pilih skenario', 'Respons yang diusulkan', 'Dampak KPI', 'Alasan rekomendasi'],
  },
} as const;

type Mode = keyof typeof MODES;

export function DssPage() {
  const [mode, setMode] = useState<Mode>('global');
  const selected = MODES[mode];
  return (
    <>
      <div className="segmented dss-mode-switch" role="group" aria-label="Mode DSS">
        {(Object.keys(MODES) as Mode[]).map((key) => (
          <button key={key} type="button" className={mode === key ? 'active' : undefined} aria-pressed={mode === key} onClick={() => setMode(key)}>
            {MODES[key].label}
          </button>
        ))}
      </div>
      <ModulePlaceholder title={selected.title} description={selected.description} areas={selected.areas} />
    </>
  );
}