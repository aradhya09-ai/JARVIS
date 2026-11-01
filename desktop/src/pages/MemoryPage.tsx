import { useCallback, useEffect, useState } from 'react';

import { api } from '../services/api';
import type { MemoryRecord } from '../types';
import { IconTrash } from '../components/Icons';
import { ImportPanel } from '../components/ImportPanel';
import { KnowledgePanel } from '../components/KnowledgePanel';
import { PermissionBanner } from '../components/PermissionBanner';

export function MemoryPage() {
  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [enabled, setEnabled] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [knowledgeToken, setKnowledgeToken] = useState(0);

  const load = useCallback(async () => {
    try {
      const data = await api.memory();
      setMemories(data.memories as MemoryRecord[]);
      setEnabled(data.enabled);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 9000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const remove = async (key: string) => {
    try {
      await api.deleteMemory(key);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const clearAll = async () => {
    if (!window.confirm('Delete every stored memory? This cannot be undone.')) return;
    try {
      await api.clearMemory();
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  return (
    <div className="memory-page">
      {error && <PermissionBanner tone="danger" title="Memory" onDismiss={() => setError(null)}>{error}</PermissionBanner>}
      {notice && <PermissionBanner tone="info" title="Memory" onDismiss={() => setNotice(null)}>{notice}</PermissionBanner>}

      <KnowledgePanel reloadToken={knowledgeToken} />

      <ImportPanel
        onNotice={setNotice}
        onImported={() => setKnowledgeToken((token) => token + 1)}
      />

      <section className="panel">
        <header className="panel__header">
          <div>
            <h2 className="panel__title">Persistent memory</h2>
            <p className="panel__hint">
              {memories.length} stored · memory is {enabled ? 'enabled' : 'disabled'} in privacy settings.
            </p>
          </div>
          {memories.length > 0 && (
            <button type="button" className="button button--tiny button--danger" onClick={() => void clearAll()}>
              <IconTrash size={13} /> Clear all
            </button>
          )}
        </header>
        <p className="note" style={{ marginBottom: 12 }}>
          JARVIS only remembers what was explicitly saved (preferences, project context, useful
          facts). Everything is stored locally in SQLite and can be corrected or deleted here.
        </p>
        <div className="memory-list">
          {memories.length === 0 && loaded && (
            <p className="empty-note">
              Nothing stored yet. When memory is enabled, approved preferences appear here.
            </p>
          )}
          {memories.map((memory) => (
            <div key={memory.key} className="memory-item">
              <div className="memory-item__body">
                <strong>{memory.key}</strong>
                <span>{memory.kind} · updated {new Date(memory.updated_at).toLocaleString()}</span>
                <code>{JSON.stringify(memory.value)}</code>
              </div>
              <button
                type="button"
                className="icon-button icon-button--danger"
                onClick={() => void remove(memory.key)}
                title={`Delete ${memory.key}`}
                aria-label={`Delete ${memory.key}`}
              >
                <IconTrash size={14} />
              </button>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
