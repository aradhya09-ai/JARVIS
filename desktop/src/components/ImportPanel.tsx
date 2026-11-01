import { useRef, useState } from 'react';

import { api, ApiError } from '../services/api';
import type { ImportCandidate, ImportConversation } from '../types';
import { IconBolt, IconTrash } from './Icons';
import { PermissionBanner } from './PermissionBanner';

interface ImportPanelProps {
  onNotice: (message: string) => void;
  onImported: () => void;
}

function describe(cause: unknown): string {
  if (cause instanceof ApiError) return cause.message;
  return cause instanceof Error ? cause.message : String(cause);
}

const MAX_FILE_BYTES = 40_000_000;

export function ImportPanel({ onNotice, onImported }: ImportPanelProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [importId, setImportId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ImportConversation[]>([]);
  const [candidates, setCandidates] = useState<ImportCandidate[]>([]);
  const [notes, setNotes] = useState<string[]>([]);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [fileName, setFileName] = useState('');

  const reset = () => {
    setImportId(null);
    setConversations([]);
    setCandidates([]);
    setNotes([]);
    setChecked(new Set());
    setExcluded(new Set());
  };

  const readFile = async (file: File) => {
    setBusy(true);
    setError(null);
    try {
      if (file.size > MAX_FILE_BYTES) {
        throw new ApiError(
          `That file is ${(file.size / 1_000_000).toFixed(0)} MB; one import is capped at 40 MB.`,
          413,
        );
      }
      const raw = await file.text();
      const result = await api.previewImport(raw);
      setImportId(result.import_id);
      setConversations(result.conversations);
      setCandidates(result.candidates);
      setNotes(result.notes);
      // Nothing is ticked by default: approving has to be a deliberate act.
      setChecked(new Set());
      setExcluded(new Set());
      setFileName(file.name);
    } catch (cause) {
      setError(describe(cause));
      reset();
    } finally {
      setBusy(false);
    }
  };

  const toggle = (id: string) => {
    setChecked((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleConversation = (conversation: ImportConversation) => {
    setError(null);
    setExcluded((currentExcluded) => {
      const nextExcluded = new Set(currentExcluded);
      if (nextExcluded.has(conversation.id)) nextExcluded.delete(conversation.id);
      else nextExcluded.add(conversation.id);
      // Unticking a whole conversation also unticks anything drawn from it.
      const nextChecked = new Set(checked);
      if (nextExcluded.has(conversation.id)) {
        for (const fact of candidates) {
          if (fact.conversation_id === conversation.id) nextChecked.delete(fact.id);
        }
      }
      setChecked(nextChecked);
      return nextExcluded;
    });
  };

  const approve = async () => {
    if (!importId) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.approveImport({
        import_id: importId,
        fact_ids: Array.from(checked),
        excluded: Array.from(excluded),
      });
      onNotice(
        `${result.stored} fact(s) stored as unverified. ` +
        `${result.duplicates_not_written} duplicate(s) were skipped` +
        (result.conflicts ? `, ${result.conflicts} of the stored ones contradict another statement.` : '.'),
      );
      reset();
      onImported();
    } catch (cause) {
      setError(describe(cause));
    } finally {
      setBusy(false);
    }
  };

  const purge = async () => {
    if (!window.confirm('Delete every fact that came from a ChatGPT export? Facts you typed stay.')) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.deleteImported();
      onNotice(`${result.deleted} imported row(s) deleted. ${result.note}`);
      onImported();
    } catch (cause) {
      setError(describe(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel">
      <header className="panel__header">
        <div>
          <h2 className="panel__title">Import a ChatGPT export</h2>
          <p className="panel__hint">
            Read locally from conversations.json. The file is parsed in memory, you approve each
            entry, and only your own messages are offered as facts.
          </p>
        </div>
        <button type="button" className="button button--tiny button--danger" onClick={() => void purge()}>
          <IconTrash size={13} /> Delete imported data
        </button>
      </header>

      {error && (
        <PermissionBanner tone="danger" title="Import" onDismiss={() => setError(null)}>
          {error}
        </PermissionBanner>
      )}

      <div className="field__row">
        <input
          ref={inputRef}
          type="file"
          accept="application/json,.json"
          className="file-input"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void readFile(file);
          }}
        />
        {importId && (
          <button type="button" className="button button--tiny" onClick={reset}>
            Discard preview
          </button>
        )}
      </div>

      {notes.length > 0 && (
        <ul className="import-notes">
          {notes.map((note) => <li key={note}>{note}</li>)}
        </ul>
      )}

      {conversations.length > 0 && (
        <div className="chip-row">
          {conversations.map((conversation) => (
            <button
              key={conversation.id}
              type="button"
              className={`chip chip--button${excluded.has(conversation.id) ? '' : ' is-on'}`}
              onClick={() => toggleConversation(conversation)}
              title={`${conversation.user_messages} of your message(s) read${conversation.started ? `, from ${conversation.started}` : ''}`}
            >
              {excluded.has(conversation.id) ? 'excluded: ' : ''}
              {conversation.name} ({conversation.user_messages})
            </button>
          ))}
        </div>
      )}

      {candidates.length > 0 && (
        <>
          <table className="data-table">
            <thead>
              <tr><th>Store</th><th>What you said</th><th>Category</th><th>From</th><th>Flags</th></tr>
            </thead>
            <tbody>
              {candidates.map((fact) => (
                <tr key={fact.id}>
                  <td>
                    <input
                      type="checkbox"
                      checked={checked.has(fact.id)}
                      disabled={Boolean(fact.duplicate_of) || excluded.has(fact.conversation_id)}
                      onChange={() => toggle(fact.id)}
                      aria-label={`Store: ${fact.text.slice(0, 60)}`}
                    />
                  </td>
                  <td className="import-text">{fact.text}</td>
                  <td>{fact.category}</td>
                  <td>
                    {fact.conversation}
                    {fact.said_at ? ` · ${fact.said_at}` : ''}
                  </td>
                  <td>
                    {fact.duplicate_of
                      ? <span className="chip chip--warning">duplicate</span>
                      : null}
                    {fact.conflicts_with
                      ? <span className="chip chip--warning">conflicts</span>
                      : null}
                    {fact.reason ? <small className="panel__hint"> {fact.reason}</small> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="field__row">
            <button
              type="button"
              className="button button--accent"
              onClick={() => void approve()}
              disabled={busy || checked.size === 0}
            >
              <IconBolt size={13} /> Store {checked.size} selected
            </button>
            {fileName && <span className="panel__hint">Parsed {fileName} — nothing was uploaded.</span>}
          </div>
        </>
      )}

      {!importId && (
        <p className="empty-note">
          No preview loaded. In ChatGPT use Settings &rarr; Data controls &rarr; Export data, then
          pick the conversations.json from that zip. Your credentials are never read and the file
          never leaves this machine.
        </p>
      )}
    </section>
  );
}
