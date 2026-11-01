import type { Voice } from '../hooks/useVoice';
import { IconMic, IconMicOff, IconStop } from './Icons';
import { VoiceVisualizer } from './VoiceVisualizer';

interface VoiceAssistantPanelProps {
  voice: Voice;
  online: boolean;
  wakeEnabled: boolean;
}

export function VoiceAssistantPanel({ voice, online, wakeEnabled }: VoiceAssistantPanelProps) {
  // An interrupt is only possible while the microphone is actually open, so the
  // "say stop" promise has to be conditioned on permission, not on speaking.
  const canInterrupt = voice.micPermission === 'granted';

  const mode = voice.listening
    ? 'listening'
    : voice.speaking
      ? 'speaking'
      : voice.supported
        ? 'idle'
        : 'muted';

  const statusText = !voice.supported
    ? 'Speech recognition is not available in this browser (use Chrome or Edge).'
    : voice.listening
      ? 'Listening — speak your command.'
      : voice.speaking
        ? canInterrupt
          ? 'Speaking. Say "stop, JARVIS" anytime to interrupt.'
          : 'Speaking.'
        : voice.wakeActive
          ? 'Wake-word listener is running in the background.'
          : voice.wakeFault
            ? voice.wakeFault
            : wakeEnabled
              ? 'Wake word is on but not listening yet. Press Start listening or the microphone.'
              : 'Press the microphone to talk.';

  const chipClass =
    voice.listening || voice.speaking
      ? 'chip--running'
      : voice.wakeFault
        ? 'chip--failed'
        : voice.wakeActive
          ? 'chip--awaiting_confirmation'
          : '';

  return (
    <section className="panel voice-panel" aria-label="Voice assistant">
      <header className="panel__header">
        <div>
          <h2 className="panel__title">
            <IconMic size={15} /> Voice assistant
          </h2>
          <p className="panel__hint">{statusText}</p>
        </div>
        <span className={`chip ${chipClass}`}>
          {voice.listening
            ? 'Live'
            : voice.speaking
              ? 'Speaking'
              : voice.wakeFault
                ? 'Not listening'
                : voice.wakeActive
                  ? 'Wake on'
                  : 'Idle'}
        </span>
      </header>

      <div className="voice-panel__stage">
        <button
          type="button"
          className={`voice-panel__mic${voice.listening ? ' is-live' : ''}${voice.speaking ? ' is-speaking' : ''}`}
          onClick={voice.toggle}
          disabled={!online || !voice.supported}
          aria-pressed={voice.listening}
          title={voice.listening ? 'Stop listening' : 'Start push-to-talk'}
        >
          {voice.listening || voice.supported ? <IconMic size={26} /> : <IconMicOff size={26} />}
        </button>
        <div className="voice-panel__wave">
          <VoiceVisualizer level={voice.level} mode={mode} />
          <p className="voice-panel__transcript" aria-live="polite">
            {voice.transcript
              ? `“${voice.transcript}”`
              : voice.wakeActive
                ? 'Listening for the wake phrase…'
                : voice.wakeFault
                  ? 'Wake listener stopped.'
                  : wakeEnabled
                    ? 'Wake word not running'
                    : 'Press to talk'}
          </p>
        </div>
      </div>

      <div className="voice-panel__actions">
        {wakeEnabled && !voice.wakeActive && (
          <button
            type="button"
            className="button button--tiny"
            onClick={() => void voice.toggleWake()}
            disabled={!online || !voice.supported}
          >
            <IconMic size={12} /> Start wake listener
          </button>
        )}
        {voice.speaking && (
          <button type="button" className="button button--tiny button--danger" onClick={voice.stopSpeaking}>
            <IconStop size={12} /> Stop speech
          </button>
        )}
        {voice.speaking && (
          <span className="voice-panel__note">
            {!canInterrupt
              ? 'No microphone is open, so spoken interrupts cannot work. Use Stop speech.'
              : voice.speechStatus?.route === 'system'
                ? 'Audio is coming from the laptop speakers. Say “stop”, “ruk jao” or “bas” to interrupt.'
                : 'Barge-in is active — say “stop”, “ruk jao” or “bas” to interrupt.'}
          </span>
        )}
      </div>
    </section>
  );
}
