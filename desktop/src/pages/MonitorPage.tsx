import { useCallback, useEffect, useState } from 'react';

import { api } from '../services/api';
import type { Backend } from '../hooks/useBackend';
import type { Health, ProcessInfo, SystemStats, WindowInfo } from '../types';
import { IconApps, IconCpu, IconRefresh } from '../components/Icons';
import { AppLauncher } from '../components/AppLauncher';
import { SystemMonitor } from '../components/SystemMonitor';

interface MonitorPageProps {
  stats: SystemStats | null;
  health: Health | null;
  statsError: string | null;
  online: boolean;
  backend: Backend;
  onRefreshStats: () => void;
}

export function MonitorPage({ stats, health, statsError, online, backend, onRefreshStats }: MonitorPageProps) {
  const [processes, setProcesses] = useState<ProcessInfo[]>([]);
  const [windows, setWindows] = useState<WindowInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.processes();
      setProcesses(data.processes);
      setWindows(data.windows.filter((window) => window.title));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (online) void load();
  }, [online, load]);

  return (
    <div className="monitor-page">
      <SystemMonitor stats={stats} health={health} error={statsError} onRefresh={onRefreshStats} />

      <section className="panel">
        <header className="panel__header">
          <div>
            <h2 className="panel__title">
              <IconApps size={15} /> Applications
            </h2>
            <p className="panel__hint">
              Shortcuts resolve through configured paths, the Windows App Paths registry and PATH.
            </p>
          </div>
        </header>
        <AppLauncher
          online={online}
          busy={backend.busy}
          onLaunch={(name) => {
            void backend.sendCommand({ tool: 'app.open', args: { name } }, 'ui');
          }}
          onSwitch={(name) => {
            void backend.sendCommand({ tool: 'app.switch', args: { name } }, 'ui');
          }}
        />
      </section>

      <section className="panel">
        <header className="panel__header">
          <div>
            <h2 className="panel__title">
              <IconCpu size={15} /> Top processes
            </h2>
            <p className="panel__hint">Snapshot sampled by psutil; CPU may be blank on first reading.</p>
          </div>
          <button
            type="button"
            className="icon-button"
            onClick={() => void load()}
            title="Refresh processes"
            disabled={loading || !online}
          >
            <IconRefresh size={16} />
          </button>
        </header>
        {error && <p className="inline-alert">{error}</p>}
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Process</th>
                <th>PID</th>
                <th>Memory</th>
                <th>CPU</th>
              </tr>
            </thead>
            <tbody>
              {processes.map((process) => (
                <tr key={process.pid}>
                  <td>{process.name ?? 'unknown'}</td>
                  <td className="mono">{process.pid}</td>
                  <td className="mono">{process.memory_mb ? `${process.memory_mb} MB` : '—'}</td>
                  <td className="mono">
                    {process.cpu_percent === null ? '—' : `${process.cpu_percent}%`}
                  </td>
                </tr>
              ))}
              {processes.length === 0 && (
                <tr>
                  <td colSpan={4} className="empty-note">
                    {online ? 'No process data yet.' : 'Backend offline.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <header className="panel__header">
          <div>
            <h2 className="panel__title">
              <IconApps size={15} /> Visible windows
            </h2>
            <p className="panel__hint">Read from the Windows window manager (EnumWindows).</p>
          </div>
        </header>
        <div className="window-list">
          {windows.map((window) => (
            <div key={window.hwnd} className="window-item">
              <div>
                <p>{window.title}</p>
                <span>
                  {window.process ?? 'unknown'} · pid {window.pid}
                </span>
              </div>
            </div>
          ))}
          {windows.length === 0 && (
            <p className="empty-note">{online ? 'No visible windows.' : 'Backend offline.'}</p>
          )}
        </div>
      </section>

      <section className="panel">
        <header className="panel__header">
          <div>
            <h2 className="panel__title">Operating system</h2>
            <p className="panel__hint">Reported directly by Python's platform module.</p>
          </div>
        </header>
        <dl className="os-grid">
          <div>
            <dt>System</dt>
            <dd>
              {stats ? `${stats.os.system} ${stats.os.release}` : 'Unavailable'}
            </dd>
          </div>
          <div>
            <dt>Version</dt>
            <dd>{stats?.os.version ?? 'Unavailable'}</dd>
          </div>
          <div>
            <dt>Machine</dt>
            <dd>{stats?.os.machine ?? 'Unavailable'}</dd>
          </div>
          <div>
            <dt>Hostname</dt>
            <dd>{stats?.os.hostname ?? 'Unavailable'}</dd>
          </div>
          <div>
            <dt>Python</dt>
            <dd>{stats?.os.python ?? 'Unavailable'}</dd>
          </div>
          <div>
            <dt>Uptime</dt>
            <dd>{stats?.uptime.humanized ?? 'Unavailable'}</dd>
          </div>
          <div>
            <dt>Disk</dt>
            <dd>
              {stats
                ? `${stats.disk.used_gb.toFixed(1)} GB used · ${stats.disk.free_gb.toFixed(1)} GB free`
                : 'Unavailable'}
            </dd>
          </div>
          <div>
            <dt>Network</dt>
            <dd>
              {stats
                ? stats.network.internet
                  ? `Online · ${stats.network.active_interfaces.join(', ')}`
                  : 'Offline'
                : 'Unavailable'}
            </dd>
          </div>
        </dl>
      </section>
    </div>
  );
}
