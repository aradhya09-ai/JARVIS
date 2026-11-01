import { useEffect, useRef, useState, type ReactNode } from 'react';

import { IconBrain, IconChevron, IconMic, IconMicOff, IconTarget } from './Icons';

import type { TabId } from '../types';

export interface HomeSection {
  id: TabId;
  label: string;
  hint: string;
  icon: ReactNode;
}

interface HomeMenuProps {
  sections: HomeSection[];
  online: boolean;
  wakeActive: boolean;
  wakeEnabled: boolean;
  wakePhrase: string;
  onToggleWake: () => void;
  listening: boolean;
  micSupported: boolean;
  onToggleMic: () => void;
  onNavigate: (id: TabId) => void;
  onRemember: () => void;
}

export function HomeMenu({
  sections,
  online,
  wakeActive,
  wakeEnabled,
  wakePhrase,
  onToggleWake,
  listening,
  micSupported,
  onToggleMic,
  onNavigate,
  onRemember,
}: HomeMenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    window.addEventListener('pointerdown', onPointerDown, true);
    window.addEventListener('keydown', onKeyDown, true);
    return () => {
      window.removeEventListener('pointerdown', onPointerDown, true);
      window.removeEventListener('keydown', onKeyDown, true);
    };
  }, [open]);

  const wakeLabel = wakeActive ? 'Wake word live' : wakeEnabled ? 'Wake word starting' : 'Wake word off';

  return (
    <div className="home-menu" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className={`home-menu__trigger${open ? ' is-open' : ''}`}
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-haspopup="menu"
        title="Open the section menu"
      >
        <IconChevron size={13} />
        <span>Sections</span>
      </button>

      {open && (
        <div className="home-menu__panel" role="menu" aria-label="JARVIS sections">
          <p className="home-menu__title">Navigate</p>
          {sections.map((section) => (
            <button
              key={section.id}
              type="button"
              role="menuitem"
              className="home-menu__item"
              onClick={() => {
                setOpen(false);
                onNavigate(section.id);
              }}
            >
              <span className="home-menu__icon">{section.icon}</span>
              <span className="home-menu__text">
                <strong>{section.label}</strong>
                <small>{section.hint}</small>
              </span>
            </button>
          ))}

          <p className="home-menu__title home-menu__title--rule">Memory</p>
          <button
            type="button"
            role="menuitem"
            className="home-menu__item"
            disabled={!online}
            onClick={() => {
              setOpen(false);
              onRemember();
            }}
            title={online ? 'Write or attach something to remember for good' : 'Needs the backend'}
          >
            <span className="home-menu__icon">
              <IconBrain size={16} />
            </span>
            <span className="home-menu__text">
              <strong>Remember this</strong>
              <small>Type a detail or attach a text file — kept for life</small>
            </span>
          </button>

          <p className="home-menu__title home-menu__title--rule">Voice</p>
          <button
            type="button"
            role="menuitem"
            className="home-menu__item"
            disabled={!online || !micSupported}
            onClick={() => {
              setOpen(false);
              onToggleMic();
            }}
            title={
              !micSupported
                ? 'This browser has no speech recognition engine'
                : listening
                  ? 'Stop listening now'
                  : 'Listen for one command now'
            }
          >
            <span className="home-menu__icon">{listening ? <IconMic size={16} /> : <IconMicOff size={16} />}</span>
            <span className="home-menu__text">
              <strong>{listening ? 'Listening' : 'Listen once'}</strong>
              <small>Push-to-talk for a single command</small>
            </span>
            <span className={`home-menu__state${listening ? ' home-menu__state--on' : ''}`}>
              {listening ? 'on' : 'off'}
            </span>
          </button>
          <button
            type="button"
            role="menuitem"
            className="home-menu__item"
            disabled={!online || !micSupported}
            onClick={() => {
              setOpen(false);
              onToggleWake();
            }}
            title={
              !micSupported
                ? 'Wake word needs Chrome or Edge speech recognition'
                : wakeActive
                  ? 'Stop hands-free wake word listening'
                  : `Start listening for “${wakePhrase}”`
            }
          >
            <span className="home-menu__icon">
              <IconTarget size={16} />
            </span>
            <span className="home-menu__text">
              <strong>{wakeLabel}</strong>
              <small>
                {!micSupported
                  ? 'Needs browser speech recognition'
                  : `Say “${wakePhrase}” and JARVIS answers, then listens`}
              </small>
            </span>
            <span className={`home-menu__state${wakeActive ? ' home-menu__state--on' : ''}`}>
              {wakeActive ? 'on' : 'off'}
            </span>
          </button>
        </div>
      )}
    </div>
  );
}
