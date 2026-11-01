import { useCallback, useEffect, useState } from 'react';

import { api, ApiError } from '../services/api';
import type { SiteAlias } from '../types';
import { IconPlus, IconRefresh, IconTrash } from './Icons';
import { PermissionBanner } from './PermissionBanner';

interface SiteRegistryPanelProps {
  onNotice: (message: string) => void;
}

function describe(cause: unknown): string {
  if (cause instanceof ApiError) return cause.message;
  return cause instanceof Error ? cause.message : String(cause);
}

// A bare 'instagram' is not a URL; anything saved without a real address would
// make "open Instagram" fail at launch time instead of here.
function looksLikeUrl(value: string): boolean {
  return /^https?:\/\/\S+\.\S+/.test(value.trim());
}

export function SiteRegistryPanel({ onNotice }: SiteRegistryPanelProps) {
  const [sites, setSites] = useState<SiteAlias[]>([]);
  const [form, setForm] = useState({ name: '', url: '', aliases: '' });
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.sites();
      setSites(data.sites);
      setError(null);
    } catch (cause) {
      setError(describe(cause));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async () => {
    setError(null);
    try {
      await api.saveSite({
        name: form.name.trim(),
        url: form.url.trim(),
        aliases: form.aliases.split(',').map((alias) => alias.trim()).filter(Boolean),
      });
      setForm({ name: '', url: '', aliases: '' });
      await load();
      onNotice('Website alias saved. Say "open ' + form.name.trim() + '" to use it.');
    } catch (cause) {
      setError(describe(cause));
    }
  };

  const remove = async (site: SiteAlias) => {
    setError(null);
    try {
      await api.deleteSite(site.name);
      await load();
    } catch (cause) {
      setError(describe(cause));
    }
  };

  const valid = form.name.trim().length > 1 && looksLikeUrl(form.url);

  return (
    <section className="panel">
      <header className="panel__header">
        <div>
          <h2 className="panel__title">Website aliases</h2>
          <p className="panel__hint">
            {sites.length} site(s). This is what &ldquo;open Instagram in Edge&rdquo; resolves
            against — nothing here reaches the network until you ask.
          </p>
        </div>
        <button type="button" className="button button--tiny" onClick={() => void load()}>
          <IconRefresh size={13} /> Reload
        </button>
      </header>

      {error && (
        <PermissionBanner tone="danger" title="Website aliases" onDismiss={() => setError(null)}>
          {error}
        </PermissionBanner>
      )}

      <div className="form-grid">
        <label className="field">
          <span>Name</span>
          <input
            value={form.name}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
            placeholder="e.g. Moodle"
          />
        </label>
        <label className="field">
          <span>Full URL</span>
          <input
            value={form.url}
            onChange={(event) => setForm({ ...form, url: event.target.value })}
            placeholder="https://moodle.iitpatna.ac.in"
          />
        </label>
        <label className="field">
          <span>Spoken aliases (comma separated)</span>
          <input
            value={form.aliases}
            onChange={(event) => setForm({ ...form, aliases: event.target.value })}
            placeholder="course portal,erp"
          />
        </label>
        <div className="field__row">
          <button
            type="button"
            className="button button--accent"
            onClick={() => void save()}
            disabled={!valid}
            title={form.url && !looksLikeUrl(form.url) ? 'Give the full address, starting with https://' : 'Save'}
          >
            <IconPlus size={13} /> Save alias
          </button>
        </div>
      </div>

      <ul className="site-list">
        {sites.map((site) => (
          <li key={site.name} className="site-row">
            <div>
              <strong>{site.name}</strong>
              <code>{site.url}</code>
              {site.aliases.length > 0 && <small>also: {site.aliases.join(', ')}</small>}
            </div>
            <span className="site-row__actions">
              {site.builtin && (
                <span className="chip chip--info" title="Built-in sites cannot be removed">built in</span>
              )}
              {!site.builtin && (
                <button
                  type="button"
                  className="icon-button icon-button--danger"
                  onClick={() => void remove(site)}
                  aria-label={`Remove ${site.name}`}
                  title={`Remove ${site.name}`}
                >
                  <IconTrash size={13} />
                </button>
              )}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
