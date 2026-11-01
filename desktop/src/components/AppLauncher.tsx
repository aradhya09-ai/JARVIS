import { useCallback, useEffect, useState } from 'react';

import { api } from '../services/api';
import type { AppEntry, RunningProcess, WindowInfo } from '../types';
import { IconApps, IconPlay, IconRefresh, IconSearch } from './Icons';

interface AppLauncherProps {
  online: boolean;
  busy: boolean;
  onLaunch: (name: string) => void;
  onSwitch: (name: string) => void;
}

export function AppLauncher({ online, busy, onLaunch, onSwitch }: AppLauncherProps) {
  const [apps, setApps] = useState<AppEntry[]>([]);
  const [windows, setWindows] = useState<WindowInfo[]>([]);
  const [processes, setProcesses] = useState<RunningProcess[]>([]);
  const [query, setQuery] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const loadApps = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.apps();
      setApps(data.apps);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadRunning = useCallback(async () => {
    setError(null);
    try {
      const data = await api.appsRunning();
      setWindows(data.windows.filter((window) => window.title));
      setProcesses(data.processes);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  useEffect(() => {
    if (!online) return;
    void loadApps();
    void loadRunning();
  }, [online, loadApps, loadRunning]);

  const filtered = apps.filter((app) => {
    const haystack = `${app.name} ${app.aliases.join(' ')}`.toLowerCase();
    return haystack.includes(query.trim().toLowerCase());
  });
  const favorites = filtered.filter((app) => app.favorite);
  const others = filtered.filter((app) => !app.favorite);

  return (
    <div className="apps-layout">
      <section className="panel apps-panel" aria-label="Application launcher">
        <header className="panel__header">
          <div>
            <h2 className="panel__title">Application launcher</h2>
            <p className="panel__hint">
              Allowlisted shortcuts. Missing apps can be added in the backend configuration.
            </p>
          </div>
          <button
            type="button"
            className="icon-button"
            onClick={() => void loadApps()}
            title="Refresh apps"
            disabled={!online || loading}
          >
            <IconRefresh size={16} />
          </button>
        </header>

        <label className="search-field">
          <IconSearch size={15} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Filter applications"
            aria-label="Filter applications"
          />
        </label>

        {error && <p className="inline-alert">{error}</p>}
        {!online && <p className="inline-alert">Backend offline — apps cannot be launched.</p>}

        {favorites.length > 0 && (
          <>
            <h3 className="subhead">Favourites</h3>
            <div className="app-grid">
              {favorites.map((app) => (
                <AppCard key={app.name} app={app} onLaunch={onLaunch} disabled={!online || busy} />
              ))}
            </div>
          </>
        )}

        <h3 className="subhead">Configured</h3>
        <div className="app-grid">
          {others.map((app) => (
            <AppCard key={app.name} app={app} onLaunch={onLaunch} disabled={!online || busy} />
          ))}
          {others.length === 0 && favorites.length === 0 && (
            <p className="empty-note">No applications match this filter.</p>
          )}
        </div>
      </section>

      <aside className="panel windows-panel" aria-label="Running windows">
        <header className="panel__header">
          <div>
            <h2 className="panel__title">Running now</h2>
            <p className="panel__hint">
              {windows.length} windows · {processes.length} processes
            </p>
          </div>
          <button
            type="button"
            className="icon-button"
            onClick={() => void loadRunning()}
            title="Refresh running list"
            disabled={!online}
          >
            <IconRefresh size={16} />
          </button>
        </header>
        <div className="window-list">
          {windows.length === 0 && <p className="empty-note">No visible windows detected.</p>}
          {windows.map((window) => (
            <div key={window.hwnd} className="window-item">
              <IconApps size={14} />
              <div>
                <p title={window.title}>{window.title}</p>
                <span>
                  {window.process ?? 'unknown'} · pid {window.pid}
                </span>
              </div>
              <button
                type="button"
                className="button button--tiny"
                onClick={() => onSwitch(window.process ?? window.title)}
                disabled={!online || busy}
              >
                Focus
              </button>
            </div>
          ))}
        </div>
      </aside>
    </div>
  );
}

function AppCard({
  app,
  onLaunch,
  disabled,
}: {
  app: AppEntry;
  onLaunch: (name: string) => void;
  disabled: boolean;
}) {
  return (
    <button
      type="button"
      className={`app-card${app.available ? '' : ' app-card--missing'}`}
      onClick={() => onLaunch(app.name)}
      disabled={disabled || !app.available}
      title={app.available ? app.target || app.name : `${app.name} was not found on this PC`}
    >
      <span className="app-card__icon">
        <IconPlay size={13} />
      </span>
      <span className="app-card__name">{app.name}</span>
      <span className={`status-dot status-dot--${app.available ? 'good' : 'bad'}`} />
      <span className="app-card__hint">{app.available ? 'Open' : 'Not installed'}</span>
    </button>
  );
}
