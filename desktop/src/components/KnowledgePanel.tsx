import { useCallback, useEffect, useState } from 'react';

import { api, ApiError } from '../services/api';
import type { KnowledgeItem } from '../types';
import { IconPlus, IconRefresh, IconTrash } from './Icons';
import { PermissionBanner } from './PermissionBanner';

const FALLBACK_CATEGORIES = [
  'profile', 'education', 'progress', 'goal',
  'project', 'preference', 'authorized', 'unverified',
];

function describe(cause: unknown): string {
  if (cause instanceof ApiError) return cause.message;
  return cause instanceof Error ? cause.message : String(cause);
}

function shown(value: unknown): string {
  if (typeof value === 'string') return value;
  return JSON.stringify(value);
}

interface Form {
  key: string;
  value: string;
  category: string;
  as_of: string;
  note: string;
  verified: boolean;
}

const EMPTY: Form = { key: '', value: '', category: 'preference', as_of: '', note: '', verified: true };

interface KnowledgePanelProps {
  reloadToken?: number;
}

export function KnowledgePanel({ reloadToken = 0 }: KnowledgePanelProps) {
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [categories, setCategories] = useState<string[]>(FALLBACK_CATEGORIES);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [note, setNote] = useState('');
  const [active, setActive] = useState<string>('all');
  const [form, setForm] = useState<Form>(EMPTY);
  const [editing, setEditing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.knowledge();
      setItems(data.items);
      setCategories(data.categories);
      setCounts(data.counts);
      setNote(data.note);
      setError(null);
    } catch (cause) {
      setError(describe(cause));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, reloadToken]);

  const visible = active === 'all' ? items : items.filter((item) => item.category === active);

  const submit = async () => {
    if (!form.key.trim() || !form.value.trim()) return;
    setError(null);
    const payload = {
      key: form.key.trim(),
      value: form.value.trim(),
      category: form.category,
      verified: form.verified,
      as_of: form.as_of.trim() || null,
      note: form.note.trim() || null,
    };
    try {
      if (editing) {
        await api.patchKnowledge(editing, payload);
      } else {
        await api.addKnowledge({ ...payload, source: 'user' });
      }
      setForm(EMPTY);
      setEditing(null);
      await load();
    } catch (cause) {
      setError(describe(cause));
    }
  };

  const startEdit = (item: KnowledgeItem) => {
    setEditing(item.key);
    setForm({
      key: item.key,
      value: shown(item.value),
      category: item.category,
      as_of: item.as_of ?? '',
      note: item.note ?? '',
      verified: item.verified,
    });
  };

  const remove = async (item: KnowledgeItem) => {
    if (!window.confirm(`Forget '${item.key}'?`)) return;
    setError(null);
    try {
      await api.deleteKnowledge(item.key);
      await load();
    } catch (cause) {
      setError(describe(cause));
    }
  };

  const confirmFact = async (item: KnowledgeItem) => {
    setError(null);
    try {
      await api.patchKnowledge(item.key, { verified: true, source: 'user' });
      await load();
    } catch (cause) {
      setError(describe(cause));
    }
  };

  return (
    <section className="panel">
      <header className="panel__header">
        <div>
          <h2 className="panel__title">What JARVIS knows about you</h2>
          <p className="panel__hint">{note}</p>
        </div>
        <button type="button" className="button button--tiny" onClick={() => void load()}>
          <IconRefresh size={13} /> Reload
        </button>
      </header>

      {error && (
        <PermissionBanner tone="danger" title="Knowledge" onDismiss={() => setError(null)}>
          {error}
        </PermissionBanner>
      )}

      <div className="chip-row">
        <button
          type="button"
          className={`chip chip--button${active === 'all' ? ' is-on' : ''}`}
          onClick={() => setActive('all')}
        >
          all {items.length}
        </button>
        {categories.map((category) => (
          <button
            key={category}
            type="button"
            className={`chip chip--button${active === category ? ' is-on' : ''}`}
            onClick={() => setActive(category)}
          >
            {category} {counts[category] ?? 0}
          </button>
        ))}
      </div>

      <div className="form-grid">
        <label className="field">
          <span>{editing ? 'Key (editing)' : 'Key'}</span>
          <input
            value={form.key}
            disabled={Boolean(editing)}
            onChange={(event) => setForm({ ...form, key: event.target.value })}
            placeholder="e.g. cgpa.latest"
          />
        </label>
        <label className="field">
          <span>Fact</span>
          <input
            value={form.value}
            onChange={(event) => setForm({ ...form, value: event.target.value })}
            placeholder="what you want remembered verbatim"
          />
        </label>
        <label className="field">
          <span>Category</span>
          <select value={form.category} onChange={(event) => setForm({ ...form, category: event.target.value })}>
            {categories.map((category) => <option key={category} value={category}>{category}</option>)}
          </select>
        </label>
        <label className="field">
          <span>As of (optional)</span>
          <input
            value={form.as_of}
            onChange={(event) => setForm({ ...form, as_of: event.target.value })}
            placeholder="e.g. 3rd semester, 2026"
          />
        </label>
        <label className="field">
          <span>Note (optional)</span>
          <input value={form.note} onChange={(event) => setForm({ ...form, note: event.target.value })} />
        </label>
        <label className="field field--check">
          <input
            type="checkbox"
            checked={form.verified}
            onChange={(event) => setForm({ ...form, verified: event.target.checked })}
          />
          <span>I stated this myself (verified)</span>
        </label>
        <div className="field__row">
          <button
            type="button"
            className="button button--accent"
            onClick={() => void submit()}
            disabled={!form.key.trim() || !form.value.trim()}
          >
            <IconPlus size={13} /> {editing ? 'Update' : 'Remember'}
          </button>
          {editing && (
            <button type="button" className="button" onClick={() => { setEditing(null); setForm(EMPTY); }}>
              Cancel edit
            </button>
          )}
        </div>
      </div>

      {visible.length === 0 ? (
        <p className="empty-note">
          Nothing stored under &ldquo;{active}&rdquo; yet. JARVIS does not fill this in on its own —
          it will not invent a CGPA, a semester or a project.
        </p>
      ) : (
        <ul className="knowledge-list">
          {visible.map((item) => (
            <li key={item.key} className="knowledge-item">
              <div className="knowledge-item__main">
                <strong>{item.key}</strong>
                <p>{shown(item.value)}</p>
                <span className="knowledge-item__meta">
                  <span className="chip chip--info">{item.category}</span>
                  <span className="chip chip--warning">{item.source}</span>
                  {item.verified
                    ? <span className="chip chip--good">verified</span>
                    : <span className="chip chip--warning">unverified</span>}
                  {item.as_of ? `as of ${item.as_of}` : 'no date given'}
                  {item.note ? ` · ${item.note}` : ''}
                </span>
              </div>
              <div className="knowledge-item__actions">
                {!item.verified && (
                  <button type="button" className="button button--tiny" onClick={() => void confirmFact(item)}>
                    Confirm
                  </button>
                )}
                <button type="button" className="button button--tiny" onClick={() => startEdit(item)}>
                  Edit
                </button>
                <button
                  type="button"
                  className="icon-button icon-button--danger"
                  onClick={() => void remove(item)}
                  aria-label={`Forget ${item.key}`}
                  title={`Forget ${item.key}`}
                >
                  <IconTrash size={13} />
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
