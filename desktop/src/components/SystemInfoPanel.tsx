import { useCallback, useEffect, useState } from 'react';

import { api } from '../services/api';
import type { HardwareInfo, SystemStats } from '../types';
import { IconCpu, IconMonitor, IconRefresh } from './Icons';

interface SystemInfoPanelProps {
  stats: SystemStats | null;
  online: boolean;
}

export function SystemInfoPanel({ stats, online }: SystemInfoPanelProps) {
  const [hardware, setHardware] = useState<HardwareInfo | null>(null);

  const load = useCallback(async () => {
    try {
      setHardware(await api.hardware());
    } catch {
      setHardware(null);
    }
  }, []);

  useEffect(() => {
    if (online) void load();
  }, [online, load]);

  const rows: { label: string; value: string }[] = [
    { label: 'OS', value: stats ? `${stats.os.system} ${stats.os.release}` : 'Unavailable' },
    { label: 'Device', value: hardware?.device_model ?? 'Unavailable' },
    { label: 'Processor', value: hardware?.cpu_name ?? (stats?.cpu.cores ? `${stats.cpu.cores}-thread CPU` : 'Unavailable') },
    { label: 'Graphics', value: hardware && hardware.gpus.length > 0 ? hardware.gpus.join(' · ') : 'Unavailable' },
    { label: 'Memory', value: stats ? `${stats.memory.total_gb.toFixed(1)} GB RAM` : 'Unavailable' },
    { label: 'Host', value: stats?.os.hostname ?? 'Unavailable' },
  ];

  return (
    <section className="panel system-info" aria-label="System information">
      <header className="panel__header">
        <div>
          <h2 className="panel__title">
            <IconMonitor size={15} /> System info
          </h2>
          <p className="panel__hint">Reported by Windows — no invented values.</p>
        </div>
        <button
          type="button"
          className="icon-button"
          onClick={() => void load()}
          disabled={!online}
          title="Refresh system information"
        >
          <IconRefresh size={14} />
        </button>
      </header>
      <div className="system-info__grid">
        {rows.map((row) => (
          <div key={row.label} className="kv">
            <span>
              <IconCpu size={11} /> {row.label}
            </span>
            <strong>{row.value}</strong>
          </div>
        ))}
      </div>
    </section>
  );
}
