import { useEffect, useRef, useState } from 'react';

import { api, ApiError } from '../services/api';
import { IconBrain, IconClose, IconFolder } from './Icons';

// Mirrors KNOWLEDGE_CATEGORIES on the backend; an unknown value is rejected
// there with the real list, so this only has to be a sensible default.
const CATEGORIES = [
  'profile', 'education', 'schedule', 'progress', 'goal',
  'project', 'preference', 'authorized', 'unverified',
];

// KnowledgeUpload caps content at 400,000 characters.
const UPLOAD_LIMIT = 400_000;

const TEXT_ACCEPT = '.txt,.md,.markdown,.csv,.tsv,.json,.log,.html,.htm,.xml,.ini,.yaml,.yml,.srt,.sql,.py,.js,.ts,.rst';

interface RememberCaptureProps {
  online: boolean;
  onClose: () => void;
  onSaved: (summary: string) => void;
}

function memoryKey(text: string): string {
  const slug = text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 48);
  return `note.${slug || 'capture'}`;
}

export function RememberCapture({ online, onClose, onSaved }: RememberCaptureProps) {
  const [text, setText] = useState('');
  const [category, setCategory] = useState('unverified');
  const [file, setFile] = useState<File | null>(null);
  const [fileText, setFileText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const textRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    textRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const pickFile = async (chosen: File | null) => {
    setSaved(null);
    setError(null);
    if (!chosen) {
      setFile(null);
      setFileText('');
      return;
    }
    setFile(chosen);
    setFileText('');
    if (chosen.size > UPLOAD_LIMIT * 4) {
      setError(`${chosen.name} is too large to remember as text.`);
      return;
    }
    try {
      const content = await chosen.text();
      if (!content.trim()) {
        setError(`${chosen.name} has no text to remember.`);
        return;
      }
      setFileText(content.slice(0, UPLOAD_LIMIT));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      if (file) {
        const data = await api.uploadKnowledge({
          filename: file.name,
          content: fileText,
          category,
        });
        const suffix = data.truncated ? ' (trimmed to the first 60,000 characters)' : '';
        setSaved(`Remembered ${data.item.key} from ${file.name}${suffix}.`);
        onSaved(`Remembered ${file.name} as ${data.item.key}.`);
        setFile(null);
        setFileText('');
      } else {
        const value = text.trim();
        if (!value) return;
        const data = await api.addKnowledge({
          key: memoryKey(value),
          value,
          category,
          source: 'user',
          verified: true,
        });
        setSaved(`Remembered as ${data.item.key}. It stays in the local database until you forget it in Memory.`);
        onSaved(`Remembered: ${value.slice(0, 80)}.`);
        setText('');
      }
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const canSave = online && !busy && Boolean(file ? fileText : text.trim());

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal remember"
        role="dialog"
        aria-modal="true"
        aria-labelledby="remember-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="remember__head">
          <span className="remember__icon">
            <IconBrain size={18} />
          </span>
          <div>
            <h2 id="remember-title">Remember this</h2>
            <p>Type it, or attach a text file. JARVIS keeps it locally for the lifetime of the database.</p>
          </div>
          <button type="button" className="icon-button remember__close" onClick={onClose} aria-label="Close">
            <IconClose size={14} />
          </button>
        </header>

        <label className="field">
          <span>What to remember</span>
          <textarea
            ref={textRef}
            rows={4}
            value={text}
            disabled={busy || Boolean(file)}
            onChange={(event) => {
              setText(event.target.value);
              setSaved(null);
            }}
            placeholder="e.g. my DBMS lecturer is Dr. Sharma and classes are on Tuesday at 10"
          />
        </label>

        <div className="remember__row">
          <label className="field field--grow">
            <span>Category</span>
            <select value={category} onChange={(event) => setCategory(event.target.value)}>
              {CATEGORIES.map((item) => (
                <option key={item} value={item}>{item}</option>
              ))}
            </select>
          </label>
          <div className="field field--grow">
            <span>Attach a file instead</span>
            <div className="field__row">
              <input
                id="remember-file"
                type="file"
                className="remember__file-input"
                accept={TEXT_ACCEPT}
                onChange={(event) => void pickFile(event.target.files?.[0] ?? null)}
              />
              <label htmlFor="remember-file" className="button button--tiny">
                <IconFolder size={13} />
                {file ? 'Change' : 'Choose'}
              </label>
              <small className="remember__file-name">
                {file ? `${file.name} · ${fileText.length.toLocaleString()} characters read` : 'text, markdown, csv, json, logs, code'}
              </small>
            </div>
            {file && (
              <button type="button" className="button button--tiny" onClick={() => void pickFile(null)}>
                Remove file
              </button>
            )}
          </div>
        </div>

        <p className="remember__hint">
          PDFs, Word documents and images are not read yet — there is no parser installed, so paste
          their text instead of uploading them.
        </p>

        {error && <p className="remember__error">{error}</p>}
        {saved && <p className="remember__saved">{saved}</p>}

        <div className="modal__actions">
          <button type="button" className="button button--ghost" onClick={onClose} disabled={busy}>
            Close
          </button>
          <button type="button" className="button button--accent" onClick={() => void save()} disabled={!canSave}>
            {busy ? 'Saving…' : file ? 'Remember this file' : 'Remember'}
          </button>
        </div>
      </div>
    </div>
  );
}
