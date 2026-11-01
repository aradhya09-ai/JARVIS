import { useEffect, useRef } from 'react';

import type { Message } from '../types';
import { IconSpark } from './Icons';

interface ConversationProps {
  messages: Message[];
  thinking: boolean;
  onSpeak: (text: string) => void;
  limit?: number;
}

function formatTime(raw: string): string {
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export function Conversation({ messages, thinking, onSpeak, limit = 60 }: ConversationProps) {
  const listRef = useRef<HTMLDivElement | null>(null);
  const visible = messages.slice(-limit);
  const lastCount = visible.length;

  useEffect(() => {
    const node = listRef.current;
    if (!node) return;
    const nearBottom = node.scrollHeight - node.scrollTop - node.clientHeight < 160;
    if (nearBottom) node.scrollTop = node.scrollHeight;
  }, [lastCount, thinking]);

  return (
    <div className="conversation" ref={listRef} aria-live="polite">
      {visible.length === 0 && !thinking && (
        <div className="conversation__empty">
          <IconSpark size={22} />
          <p>No conversation yet.</p>
          <span>Try “check my battery”, “organize Downloads”, or “make a presentation on AI”.</span>
        </div>
      )}
      {visible.map((message) => (
        <article key={message.id} className={`bubble bubble--${message.role}`}>
          <header className="bubble__meta">
            <span className="bubble__author">{message.role === 'user' ? 'You' : 'JARVIS'}</span>
            <time>{formatTime(message.at)}</time>
            {message.role === 'assistant' && (
              <button
                type="button"
                className="bubble__speak"
                onClick={() => onSpeak(message.content)}
                title="Speak this reply"
                aria-label="Speak this reply"
              >
                ▶
              </button>
            )}
          </header>
          <p className="bubble__content">{message.content}</p>
        </article>
      ))}
      {thinking && (
        <div className="bubble bubble--assistant bubble--thinking">
          <span className="thinking-dots" aria-label="JARVIS is processing">
            <i />
            <i />
            <i />
          </span>
          <span>Processing intent…</span>
        </div>
      )}
    </div>
  );
}
