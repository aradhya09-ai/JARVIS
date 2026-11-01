import { useRef, useState, type FormEvent, type KeyboardEvent } from 'react';

import { IconMic, IconMicOff, IconSend } from './Icons';

interface CommandInputProps {
  onSubmit: (text: string) => void;
  onToggleMic: () => void;
  micSupported: boolean;
  listening: boolean;
  busy: boolean;
  online: boolean;
  compact?: boolean;
  autoFocus?: boolean;
}

export function CommandInput({
  onSubmit,
  onToggleMic,
  micSupported,
  listening,
  busy,
  online,
  compact = false,
  autoFocus = false,
}: CommandInputProps) {
  const [value, setValue] = useState('');
  const inputRef = useRef<HTMLInputElement | null>(null);

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    const text = value.trim();
    if (!text || busy || !online) return;
    onSubmit(text);
    setValue('');
    inputRef.current?.focus();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <form className={`command${compact ? ' command--compact' : ''}`} onSubmit={submit}>
      <span className="command__prompt" aria-hidden="true">
        &gt;_
      </span>
      <input
        ref={inputRef}
        className="command__input"
        value={value}
        autoFocus={autoFocus}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={onKeyDown}
        placeholder={
          online
            ? listening
              ? 'Listening — speak or type a command'
              : 'Give JARVIS a command, e.g. “open VS Code”'
            : 'Backend offline — start the API to send commands'
        }
        aria-label="Command input"
        disabled={!online}
        maxLength={2000}
        autoComplete="off"
        spellCheck={false}
      />
      <div className="command__actions">
        <button
          type="button"
          className={`icon-button${listening ? ' icon-button--live' : ''}`}
          onClick={onToggleMic}
          disabled={!online || !micSupported}
          title={
            micSupported
              ? listening
                ? 'Stop listening'
                : 'Start push-to-talk (microphone)'
              : 'Speech recognition is not available in this browser'
          }
          aria-pressed={listening}
        >
          {micSupported ? <IconMic size={18} /> : <IconMicOff size={18} />}
        </button>
        <button
          type="submit"
          className="icon-button icon-button--accent"
          disabled={!value.trim() || busy || !online}
          title={online ? 'Send command' : 'Backend offline'}
          aria-label="Send command"
        >
          <IconSend size={17} />
        </button>
      </div>
    </form>
  );
}
