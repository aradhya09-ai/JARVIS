import { useEffect, useRef, useState } from 'react';

import type { ReactorState, Task } from '../types';
import { CommandInput } from './CommandInput';
import { IconClose, IconDashboard, IconMic, IconMicOff } from './Icons';

interface FloatingOrbProps {
  state: ReactorState;
  online: boolean;
  listening: boolean;
  micSupported: boolean;
  busy: boolean;
  lastMessage: string | null;
  activeTask: Task | null;
  onSubmit: (text: string) => void;
  onToggleMic: () => void;
  onOpenDashboard: () => void;
}

export function FloatingOrb({
  state,
  online,
  listening,
  micSupported,
  busy,
  lastMessage,
  activeTask,
  onSubmit,
  onToggleMic,
  onOpenDashboard,
}: FloatingOrbProps) {
  const [open, setOpen] = useState(false);
  const panelRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  return (
    <div className={`orb${open ? ' orb--open' : ''}`}>
      {open && (
        <div className="orb__panel" ref={panelRef}>
          <header className="orb__head">
            <div>
              <span className="orb__eyebrow">J.A.R.V.I.S.</span>
              <strong className={`orb__status orb__status--${state}`}>
                {online ? state : 'offline'}
              </strong>
            </div>
            <button
              type="button"
              className="icon-button"
              onClick={() => setOpen(false)}
              aria-label="Minimise assistant"
            >
              <IconClose size={14} />
            </button>
          </header>

          <div className="orb__body">
            {activeTask ? (
              <p className="orb__line">
                <span className="chip chip--running">{activeTask.status}</span> {activeTask.text}
              </p>
            ) : (
              <p className="orb__line orb__line--muted">{lastMessage ?? 'Standing by, sir.'}</p>
            )}
          </div>

          <CommandInput
            compact
            busy={busy}
            online={online}
            listening={listening}
            micSupported={micSupported}
            onSubmit={onSubmit}
            onToggleMic={onToggleMic}
            autoFocus
          />

          <div className="orb__actions">
            <button
              type="button"
              className={`button button--tiny${listening ? ' button--live' : ''}`}
              onClick={onToggleMic}
              disabled={!micSupported || !online}
            >
              {listening ? <IconMicOff size={13} /> : <IconMic size={13} />}
              {listening ? 'Stop mic' : 'Talk'}
            </button>
            <button
              type="button"
              className="button button--tiny button--ghost"
              onClick={() => {
                onOpenDashboard();
                setOpen(false);
              }}
            >
              <IconDashboard size={13} /> Command deck
            </button>
          </div>
        </div>
      )}

      <button
        type="button"
        className={`orb__button orb__button--${state}${listening ? ' is-listening' : ''}`}
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-label={open ? 'Close floating assistant' : 'Open floating assistant'}
        title="JARVIS floating assistant"
      >
        <span className="orb__ring orb__ring--1" />
        <span className="orb__ring orb__ring--2" />
        <span className="orb__core" />
      </button>
    </div>
  );
}
