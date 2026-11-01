import type { Health, SystemStats } from '../types';
import { IconBattery, IconCpu, IconPlug, IconRefresh, IconWifi } from './Icons';

interface SystemMonitorProps {
  stats: SystemStats | null;
  health: Health | null;
  error?: string | null;
  onRefresh?: () => void;
  compact?: boolean;
}

function formatGb(value: number | undefined): string {
  return typeof value === 'number' ? `${value.toFixed(1)} GB` : 'Unavailable';
}

function Meter({ value, tone = 'accent' }: { value: number; tone?: 'accent' | 'warn' | 'danger' }) {
  const clamped = Math.max(0, Math.min(100, value));
  return (
    <div className={`meter meter--${tone}`} role="presentation">
      <span style={{ width: `${clamped}%` }} />
    </div>
  );
}

export function SystemMonitor({ stats, health, error, onRefresh, compact = false }: SystemMonitorProps) {
  const uptime = stats?.uptime?.humanized ?? 'Unavailable';
  const battery = stats?.battery;
  const batteryPercent = battery?.available ? (battery.percent ?? 0) : null;
  const online = stats?.network?.internet;
  const activeInterfaces = stats?.network?.active_interfaces?.join(', ') ?? 'Unavailable';

  return (
    <section className={`panel monitor${compact ? ' monitor--compact' : ''}`} aria-label="System monitor">
      <header className="panel__header">
        <div>
          <h2 className="panel__title">System telemetry</h2>
          <p className="panel__hint">
            Live readings from Windows ·{' '}
            {stats ? new Date(stats.timestamp).toLocaleTimeString() : 'waiting for backend'}
          </p>
        </div>
        {onRefresh && (
          <button type="button" className="icon-button" onClick={onRefresh} title="Refresh telemetry">
            <IconRefresh size={16} />
          </button>
        )}
      </header>

      {(error || stats?.error || health?.status === 'degraded') && (
        <p className="inline-alert">{error || stats?.error || 'Backend database is degraded.'}</p>
      )}

      <div className="metric-grid">
        <article className="metric">
          <span className="metric__icon">
            <IconCpu size={16} />
          </span>
          <div className="metric__body">
            <div className="metric__row">
              <span className="metric__label">CPU</span>
              <strong>{stats ? `${stats.cpu.percent}%` : '—'}</strong>
            </div>
            <Meter value={stats?.cpu.percent ?? 0} />
            <span className="metric__foot">
              {stats?.cpu.cores ?? '—'} threads
              {stats?.cpu.frequency_mhz ? ` · ${Math.round(stats.cpu.frequency_mhz)} MHz` : ''}
            </span>
          </div>
        </article>

        <article className="metric">
          <span className="metric__icon">
            <IconCpu size={16} />
          </span>
          <div className="metric__body">
            <div className="metric__row">
              <span className="metric__label">Memory</span>
              <strong>{stats ? `${stats.memory.percent}%` : '—'}</strong>
            </div>
            <Meter
              value={stats?.memory.percent ?? 0}
              tone={stats && stats.memory.percent > 88 ? 'danger' : 'accent'}
            />
            <span className="metric__foot">
              {stats
                ? `${formatGb(stats.memory.used_gb)} / ${formatGb(stats.memory.total_gb)}`
                : 'Unavailable'}
            </span>
          </div>
        </article>

        <article className="metric">
          <span className="metric__icon">
            {battery?.plugged_in ? <IconPlug size={16} /> : <IconBattery size={16} />}
          </span>
          <div className="metric__body">
            <div className="metric__row">
              <span className="metric__label">Battery</span>
              <strong>{batteryPercent !== null ? `${batteryPercent}%` : 'Unavailable'}</strong>
            </div>
            <Meter
              value={batteryPercent ?? 0}
              tone={batteryPercent !== null && batteryPercent < 20 ? 'danger' : 'accent'}
            />
            <span className="metric__foot">
              {battery?.available
                ? `${battery.plugged_in ? 'Charging' : 'On battery'}${
                    battery.time_left_minutes ? ` · ${battery.time_left_minutes} min left` : ''
                  }`
                : battery?.reason ?? 'Unavailable'}
            </span>
          </div>
        </article>

        <article className="metric">
          <span className="metric__icon">
            <IconWifi size={16} />
          </span>
          <div className="metric__body">
            <div className="metric__row">
              <span className="metric__label">Network</span>
              <strong>{online === undefined ? '—' : online ? 'Online' : 'Offline'}</strong>
            </div>
            <span className={`status-dot status-dot--${online ? 'good' : 'bad'}`} />
            <span className="metric__foot" title={activeInterfaces}>
              {activeInterfaces}
            </span>
          </div>
        </article>
      </div>

      <div className="monitor__footer">
        <div className="kv">
          <span>Disk</span>
          <strong>
            {stats
              ? `${stats.disk.free_gb.toFixed(1)} GB free of ${stats.disk.total_gb.toFixed(0)} GB`
              : 'Unavailable'}
          </strong>
        </div>
        <div className="kv">
          <span>Uptime</span>
          <strong>{uptime}</strong>
        </div>
        <div className="kv">
          <span>Processes</span>
          <strong>{stats?.process_count ?? 'Unavailable'}</strong>
        </div>
        <div className="kv">
          <span>Host</span>
          <strong>{stats?.os.hostname ?? 'Unavailable'}</strong>
        </div>
      </div>
      {stats && (
        <div className="disk-meter">
          <Meter value={stats.disk.percent} tone={stats.disk.percent > 90 ? 'danger' : 'accent'} />
        </div>
      )}
    </section>
  );
}
