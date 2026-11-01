import type { Backend } from '../hooks/useBackend';
import type { Voice } from '../hooks/useVoice';
import { CommandInput } from '../components/CommandInput';
import { Conversation } from '../components/Conversation';
import { IconTrash } from '../components/Icons';
import { PermissionBanner } from '../components/PermissionBanner';
import { VoiceVisualizer } from '../components/VoiceVisualizer';

interface ChatPageProps {
  backend: Backend;
  voice: Voice;
  onSend: (text: string, source?: string) => void;
  onSpeak: (text: string) => void;
}

export function ChatPage({ backend, voice, onSend, onSpeak }: ChatPageProps) {
  const online = backend.connection === 'online';
  const modelOnline = Boolean(backend.health?.active_provider);

  return (
    <div className="chat-page">
      {!modelOnline && online && (
        <PermissionBanner tone="info" title="Running on deterministic rules">
          No language model is connected, so open-ended chat falls back to pattern matching. Start
          Ollama (for example <code>ollama serve</code> with <code>qwen2.5:3b</code>) for natural
          English and Hinglish conversation.
        </PermissionBanner>
      )}

      <section className="panel conversation-panel conversation-panel--page">
        <header className="panel__header">
          <div>
            <h2 className="panel__title">AI chat</h2>
            <p className="panel__hint">
              One shared conversation across chat and voice — same context, same JARVIS.
            </p>
          </div>
          <button
            type="button"
            className="icon-button"
            onClick={() => void backend.clearConversation()}
            title="Clear conversation"
            aria-label="Clear conversation"
          >
            <IconTrash size={15} />
          </button>
        </header>
        <Conversation
          messages={backend.messages}
          thinking={backend.busy}
          onSpeak={onSpeak}
          limit={200}
        />
        <div className="chat-page__composer">
          <VoiceVisualizer
            level={voice.level}
            mode={voice.listening ? 'listening' : voice.speaking ? 'speaking' : 'idle'}
            height={42}
          />
          <CommandInput
            onSubmit={(text) => onSend(text, 'text')}
            onToggleMic={voice.toggle}
            micSupported={voice.supported}
            listening={voice.listening}
            busy={backend.busy}
            online={online}
          />
          {voice.speaking && (
            <button type="button" className="button button--tiny button--danger" onClick={voice.stopSpeaking}>
              Stop speaking
            </button>
          )}
        </div>
      </section>
    </div>
  );
}
