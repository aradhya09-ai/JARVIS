import { useEffect, useRef } from 'react';

import type { PendingConfirmation } from '../hooks/useBackend';
import { IconShield, IconWarning } from './Icons';

interface ConfirmDialogProps {
  confirmation: PendingConfirmation;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({ confirmation, busy, onConfirm, onCancel }: ConfirmDialogProps) {
  const confirmRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    confirmRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCancel();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onCancel]);

  const high = confirmation.risk === 'high';

  return (
    <div className="modal-backdrop" role="presentation" onClick={onCancel}>
      <div
        className="modal confirm"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="confirm__head">
          <span className={`confirm__icon${high ? ' confirm__icon--high' : ''}`}>
            {high ? <IconWarning size={20} /> : <IconShield size={20} />}
          </span>
          <div>
            <h2 id="confirm-title">Authorisation required</h2>
            <p>
              <span className={`risk risk--${confirmation.risk}`}>{confirmation.risk} risk</span>
              <span className="confirm__tool">{confirmation.task?.tool}</span>
            </p>
          </div>
        </header>

        <p className="confirm__summary">{confirmation.summary}</p>
        {confirmation.task && <p className="confirm__command">“{confirmation.task.text}”</p>}

        <p className="confirm__note">
          JARVIS will only execute this exact request. Nothing else is approved by confirming.
          {high ? ' High-risk actions cannot be auto-confirmed.' : ''}
        </p>

        <div className="modal__actions">
          <button type="button" className="button button--ghost" onClick={onCancel} disabled={busy}>
            Cancel request
          </button>
          <button
            ref={confirmRef}
            type="button"
            className="button button--accent"
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? 'Authorising…' : 'Confirm & run'}
          </button>
        </div>
      </div>
    </div>
  );
}
