import type { Backend } from '../hooks/useBackend';
import type { Voice } from '../hooks/useVoice';
import type { SystemStats, TabId } from '../types';
import { CommandInput } from '../components/CommandInput';
import { Conversation } from '../components/Conversation';
import { CurrentTaskPanel } from '../components/CurrentTaskPanel';
import { IconBell, IconRefresh, IconSpark, IconTrash } from '../components/Icons';
import { PermissionBanner } from '../components/PermissionBanner';
import { PhoneStatus } from '../components/PhoneStatus';
import { QuickActions } from '../components/QuickActions';
import { RecentCommands } from '../components/RecentCommands';
import { SystemInfoPanel } from '../components/SystemInfoPanel';
import { SystemMonitor } from '../components/SystemMonitor';
import { VoiceAssistantPanel } from '../components/VoiceAssistantPanel';

interface CommandPageProps {
  backend: Backend;
  voice: Voice;
  stats: SystemStats | null;
  statsError: string | null;
  wakeEnabled: boolean;
  onToggleWake: () => void;
  onSend: (text: string, source?: string) => void;
  onSpeak: (text: string) => void;
  onRefreshStats: () => void;
  onNavigate: (tab: TabId) => void;
}

export function CommandPage({
  backend,
  voice,
  stats,
  statsError,
  wakeEnabled,
  onToggleWake,
  onSend,
  onSpeak,
  onRefreshStats,
  onNavigate,
}: CommandPageProps) {
  const online = backend.connection === 'online';
  const activeTask =
    backend.tasks.find((task) => ['pending', 'running', 'awaiting_confirmation'].includes(task.status)) ??
    null;
  const lastTask =
    backend.tasks.find((task) => ['completed', 'failed', 'cancelled'].includes(task.status)) ?? null;

  const bannerTitle = !online
    ? 'Backend offline'
    : voice.listening
      ? 'Microphone is live'
      : voice.wakeActive
        ? 'Wake word listener active'
        : null;

  const bannerTone = !online ? 'danger' : 'info';
  const bannerText = !online
    ? 'Start the FastAPI backend on 127.0.0.1:8765. Commands are disabled until it reconnects.'
    : voice.wakeActive
      ? `Listening for “${String(backend.settings['voice.wake_word'] ?? 'hey jarvis')}”. The microphone stays active until you stop wake mode.`
      : 'Audio is used only for speech recognition and stops when listening ends.';

  return (
    <div className="dashboard">
      <div className="dashboard__left">
        <SystemMonitor
          compact
          stats={stats}
          health={backend.health}
          error={statsError}
          onRefresh={onRefreshStats}
        />
        <QuickActions onRun={(text) => onSend(text, 'quick_action')} disabled={!online || backend.busy} />
        <RecentCommands tasks={backend.tasks} onViewAll={() => onNavigate('automation')} />
      </div>

      <div className="dashboard__center">
        {bannerTitle ? (
          <PermissionBanner
            tone={bannerTone}
            title={bannerTitle}
            action={!online ? { label: 'Retry', onClick: () => void backend.refresh() } : undefined}
          >
            {bannerText}
          </PermissionBanner>
        ) : null}

        {voice.error && (
          <PermissionBanner tone="warning" title="Voice" onDismiss={voice.clearError}>
            {voice.error}
          </PermissionBanner>
        )}

        {backend.error && (
          <PermissionBanner tone="danger" title="Command failed" onDismiss={backend.dismissError}>
            {backend.error}
          </PermissionBanner>
        )}

        <section className="panel core-panel">
          <div className="core-panel__controls">
            <header className="core-panel__head">
              <div>
                <span className="eyebrow">Just A Rather Very Intelligent System</span>
                <h1>Command console</h1>
              </div>
              <span className={`chip chip--${online ? 'online' : 'offline'}`}>
                {online ? 'Linked' : 'Offline'}
              </span>
            </header>

            <dl className="core-stats">
              <div>
                <dt>Model</dt>
                <dd>{backend.health?.active_provider ?? 'Deterministic rules'}</dd>
              </div>
              <div>
                <dt>Phone</dt>
                <dd>{backend.phone?.connected ? backend.phone.name ?? 'Linked' : 'Not connected'}</dd>
              </div>
              <div>
                <dt>Active tasks</dt>
                <dd>{backend.tasks.filter((task) => ['pending', 'running', 'awaiting_confirmation'].includes(task.status)).length}</dd>
              </div>
              <div>
                <dt>Uptime</dt>
                <dd>{backend.health ? `${Math.floor(backend.health.uptime_seconds / 60)}m` : '—'}</dd>
              </div>
            </dl>

            <CommandInput
              onSubmit={(text) => onSend(text, 'text')}
              onToggleMic={voice.toggle}
              micSupported={voice.supported}
              listening={voice.listening}
              busy={backend.busy}
              online={online}
            />

            <div className="core-panel__voice-actions">
              <button
                type="button"
                className={`button button--tiny${voice.listening ? ' button--live' : ''}`}
                onClick={() => voice.toggle()}
                disabled={!online || !voice.supported}
                title={voice.listening ? 'Stop the microphone' : 'Listen for one spoken command'}
              >
                {voice.listening ? 'Stop listening' : 'Start listening'}
              </button>
              <button
                type="button"
                className={`button button--tiny${voice.wakeActive ? ' button--live' : ''}`}
                onClick={onToggleWake}
                disabled={!online || !voice.supported}
                title="Toggle hands-free wake word listening"
              >
                <IconSpark size={13} />
                {voice.wakeActive
                  ? 'Wake word on'
                  : voice.wakeFault
                    ? 'Wake listener stopped'
                    : wakeEnabled
                      ? 'Wake word starting'
                      : 'Wake word off'}
              </button>
              {voice.speaking && (
                <button type="button" className="button button--tiny button--ghost" onClick={voice.stopSpeaking}>
                  Stop speech
                </button>
              )}
            </div>

            <p className="core-panel__voice-status">
              <span className={`voice-dot${voice.listening || voice.wakeActive ? ' voice-dot--on' : ''}`} />
              {!voice.supported
                ? 'This browser has no speech recognition support.'
                : voice.listening
                  ? 'Microphone live — speak now.'
                  : voice.wakeActive
                    ? voice.armed
                      ? 'Wake word heard — waiting for your command.'
                      : `Idle — say “${String(backend.settings['voice.wake_word'] ?? 'hey jarvis')}”.`
                    : voice.wakeFault
                      ? voice.wakeFault
                      : 'Push to talk · Enter to send'}
              {voice.speechStatus ? ` · ${voice.speechStatus.message}` : ''}
            </p>
          </div>
        </section>

        <section className="panel conversation-panel" aria-label="Conversation">
          <header className="panel__header">
            <div>
              <h2 className="panel__title">Conversation</h2>
              <p className="panel__hint">Every reply comes from a real tool result or planner response.</p>
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
          <Conversation messages={backend.messages} thinking={backend.busy} onSpeak={onSpeak} limit={60} />
        </section>

        <div className="dashboard__row">
          <CurrentTaskPanel
            activeTask={activeTask}
            lastTask={lastTask}
            health={backend.health}
            online={online}
            onCancel={(id) => void backend.cancelTask(id)}
          />
          <VoiceAssistantPanel voice={voice} online={online} wakeEnabled={wakeEnabled} />
        </div>
      </div>

      <div className="dashboard__right">
        <PhoneStatus phone={backend.phone} onPair={() => onNavigate('phone')} compact />

        <section className="panel notifications" aria-label="Notifications">
          <header className="panel__header">
            <div>
              <h2 className="panel__title">
                <IconBell size={15} /> Alerts
              </h2>
              <p className="panel__hint">{backend.unread} unread</p>
            </div>
            <div className="notifications__actions">
              <button
                type="button"
                className="icon-button"
                onClick={() => void backend.markNotificationsRead()}
                title="Mark all read"
                disabled={backend.unread === 0}
              >
                <IconRefresh size={14} />
              </button>
            </div>
          </header>
          <div className="notifications__list">
            {backend.notifications.length === 0 && <p className="empty-note">No notifications yet.</p>}
            {backend.notifications.slice(0, 4).map((item) => (
              <article key={item.id} className={`notice${item.read ? '' : ' notice--unread'}`}>
                <span>{item.source}</span>
                <strong>{item.title}</strong>
                <p>{item.body}</p>
              </article>
            ))}
          </div>
        </section>

        <SystemInfoPanel stats={stats} online={online} />
      </div>
    </div>
  );
}
