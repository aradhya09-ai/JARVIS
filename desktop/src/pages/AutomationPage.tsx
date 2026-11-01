import { useCallback, useEffect, useState } from 'react';

import { api } from '../services/api';
import type { AuditEntry } from '../types';
import { IconRefresh, IconShield } from '../components/Icons';
import { RemindersPanel } from '../components/RemindersPanel';
import { TaskCenter } from '../components/TaskCenter';
import type { Backend } from '../hooks/useBackend';

const RISK_TONE: Record<string, string> = {
  low: 'risk--low',
  moderate: 'risk--moderate',
  high: 'risk--high',
};

export function AutomationPage({ backend }: { backend: Backend }) {
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const online = backend.connection === 'online';

  const loadAudit = useCallback(async () => {
    setError(null);
    try {
      const data = await api.audit(80);
      setAudit(data.entries);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  useEffect(() => {
    if (online) void loadAudit();
  }, [online, loadAudit]);

  return (
    <div className="automation-page">
      <RemindersPanel online={online} reloadToken={backend.tasks.length} />
      <TaskCenter tasks={backend.tasks} onCancel={(id) => void backend.cancelTask(id)} />

      <section className="panel audit" aria-label="Audit log">
        <header className="panel__header">
          <div>
            <h2 className="panel__title">
              <IconShield size={15} /> Audit trail
            </h2>
            <p className="panel__hint">
              Every tool execution, confirmation and pairing attempt is recorded locally.
            </p>
          </div>
          <button
            type="button"
            className="icon-button"
            onClick={() => void loadAudit()}
            title="Refresh audit log"
            disabled={!online}
          >
            <IconRefresh size={15} />
          </button>
        </header>
        {error && <p className="inline-alert">{error}</p>}
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Actor</th>
                <th>Action</th>
                <th>Risk</th>
                <th>Outcome</th>
              </tr>
            </thead>
            <tbody>
              {audit.map((entry) => (
                <tr key={entry.id}>
                  <td className="mono">{new Date(entry.at).toLocaleTimeString()}</td>
                  <td>{entry.actor}</td>
                  <td>{entry.action}</td>
                  <td>
                    <span className={RISK_TONE[entry.risk] ?? 'risk'}>{entry.risk}</span>
                  </td>
                  <td>
                    <span className={`chip chip--${entry.outcome === 'ok' ? 'completed' : 'failed'}`}>
                      {entry.outcome}
                    </span>
                  </td>
                </tr>
              ))}
              {audit.length === 0 && (
                <tr>
                  <td colSpan={5} className="empty-note">
                    {online ? 'No audit entries yet.' : 'Backend offline.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
