import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react';

import { ConfirmDialog } from './components/ConfirmDialog';
import { FloatingOrb } from './components/FloatingOrb';
import type { HomeSection } from './components/HomeMenu';
import {
  IconBell,
  IconBolt,
  IconBrain,
  IconChat,
  IconDashboard,
  IconMic,
  IconMicOff,
  IconMonitor,
  IconPhone,
  IconSettings,
  IconTasks,
} from './components/Icons';
import { PermissionBanner } from './components/PermissionBanner';
import { RememberCapture } from './components/RememberCapture';
import { useBackend } from './hooks/useBackend';
import { useSystemStats } from './hooks/useSystemStats';
import { useVoice } from './hooks/useVoice';
import { AutomationPage } from './pages/AutomationPage';
import { ChatPage } from './pages/ChatPage';
import { CommandPage } from './pages/CommandPage';
import { DashboardPage } from './pages/DashboardPage';
import { MemoryPage } from './pages/MemoryPage';
import { MonitorPage } from './pages/MonitorPage';
import { PhonePage } from './pages/PhonePage';
import { SettingsPage } from './pages/SettingsPage';
import type { ReactorState, TabId, TaskStatus, ThemeName } from './types';

const TABS: { id: TabId; label: string; icon: ReactNode; hint: string }[] = [
  { id: 'dashboard', label: 'Home', icon: <IconDashboard size={19} />, hint: 'Heads-up display' },
  { id: 'command', label: 'Command', icon: <IconBolt size={19} />, hint: 'Console, telemetry & alerts' },
  { id: 'chat', label: 'AI Chat', icon: <IconChat size={19} />, hint: 'Full conversation' },
  { id: 'monitor', label: 'Computer', icon: <IconMonitor size={19} />, hint: 'Telemetry, apps & processes' },
  { id: 'phone', label: 'Phone', icon: <IconPhone size={19} />, hint: 'Android bridge status' },
  { id: 'automation', label: 'Automation', icon: <IconTasks size={19} />, hint: 'Tasks & audit trail' },
  { id: 'memory', label: 'Memory', icon: <IconBrain size={19} />, hint: 'Stored preferences' },
  { id: 'settings', label: 'Settings', icon: <IconSettings size={19} />, hint: 'Model, voice, theme, security' },
];

const TAB_TITLES: Record<TabId, string> = {
  dashboard: 'JARVIS',
  command: 'Command deck',
  chat: 'AI chat',
  monitor: 'Computer',
  phone: 'Phone bridge',
  automation: 'Automation',
  memory: 'Memory',
  settings: 'Configuration',
};

// The Home menu is a shortcut to what he actually reaches for while standing at the
// reactor. Computer duplicates the telemetry the reactor already shows and Phone has
// no paired device, so neither belongs here — both stay on the top rail.
const HOME_SECTIONS: HomeSection[] = TABS.filter((item) =>
  ['command', 'chat', 'automation', 'memory', 'settings'].includes(item.id));

function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return (
    <span className="clock" title={now.toLocaleDateString()}>
      <strong>{now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</strong>
      <small>{now.toLocaleDateString([], { day: 'numeric', month: 'short' })}</small>
    </span>
  );
}

function chime() {
  try {
    const Ctor = window.AudioContext ?? window.webkitAudioContext;
    if (!Ctor) return;
    const context = new Ctor();
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.type = 'sine';
    oscillator.frequency.setValueAtTime(660, context.currentTime);
    oscillator.frequency.exponentialRampToValueAtTime(990, context.currentTime + 0.14);
    gain.gain.setValueAtTime(0.0001, context.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.08, context.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + 0.32);
    oscillator.connect(gain).connect(context.destination);
    oscillator.start();
    oscillator.stop(context.currentTime + 0.34);
    oscillator.onended = () => void context.close();
  } catch {
    /* audio is optional */
  }
}

export default function App() {
  const backend = useBackend();
  const [tab, setTab] = useState<TabId>('dashboard');
  const [alert, setAlert] = useState<{ tone: 'info' | 'warning' | 'danger'; title: string; body: string } | null>(
    null,
  );
  const [flash, setFlash] = useState<ReactorState | null>(null);
  const [rememberOpen, setRememberOpen] = useState(false);
  const flashTimer = useRef<number | null>(null);
  const previousStatuses = useRef<Map<string, TaskStatus>>(new Map());
  const spokenIds = useRef<Set<number>>(new Set());
  const spokenText = useRef<Map<string, number>>(new Map());
  const hydratedRef = useRef(false);
  const handleCommandRef = useRef<(text: string, source?: string) => void>(() => undefined);

  const settings = backend.settings;
  const themeName: ThemeName = settings['theme.name'] === 'amber' ? 'amber' : 'blue';
  const accent =
    themeName === 'amber'
      ? typeof settings['theme.accent'] === 'string'
        ? (settings['theme.accent'] as string)
        : '#ff8a1e'
      : '#35c8ff';
  const reducedMotion =
    settings['theme.reduced_motion'] === true ||
    (typeof window !== 'undefined' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  const monitorInterval =
    typeof settings['monitor.interval_ms'] === 'number'
      ? (settings['monitor.interval_ms'] as number)
      : 2000;
  const monitorEnabled =
    backend.connection === 'online' && settings['monitor.enabled'] !== false;

  const { stats, error: statsError, refresh: refreshStats } = useSystemStats({
    intervalMs: monitorInterval,
    enabled: monitorEnabled,
  });

  const voice = useVoice({
    language: typeof settings['voice.language'] === 'string' ? settings['voice.language'] : 'en-IN',
    wakePhrase:
      typeof settings['voice.wake_word'] === 'string' ? settings['voice.wake_word'] : 'hey jarvis',
    wakeEnabled: settings['voice.wake_word_enabled'] === true,
    ttsEnabled: settings['voice.tts_enabled'] !== false,
    voiceName: typeof settings['voice.voice_name'] === 'string' ? settings['voice.voice_name'] : '',
    sapiVoice: typeof settings['voice.sapi_voice'] === 'string' ? settings['voice.sapi_voice'] : '',
    autoListen: settings['voice.auto_listen'] === true,
    onCommand: (text) => handleCommandRef.current(text, 'voice'),
    onWake: () => {
      chime();
      setFlash('listening');
      if (flashTimer.current !== null) window.clearTimeout(flashTimer.current);
      flashTimer.current = window.setTimeout(() => setFlash(null), 1600);
    },
    // The wake loop stays open through the greeting, so the very next
    // utterance is taken as the command — no second button press.
    wakeGreeting: "Yes Ma'am.",
    onInterrupted: () => {
      if (flashTimer.current !== null) window.clearTimeout(flashTimer.current);
      setFlash('interrupted');
      flashTimer.current = window.setTimeout(() => setFlash(null), 2200);
    },
  });

  /**
   * Speak a reply once. The same answer arrives twice - in the /api/command
   * response and again over the socket, sometimes before the response lands -
   * and each speak() call cancels the one already running. The audit log showed
   * two or three syntheses per reply in the same second, which on a tab Chrome
   * had not unlocked left as no audio at all.
   */
  const speakOnce = useCallback(
    (text: string) => {
      const key = text.trim().toLowerCase().replace(/\s+/g, ' ');
      if (!key) return;
      const now = Date.now();
      const seen = spokenText.current.get(key);
      if (seen !== undefined && now - seen < 20000) return;
      spokenText.current.set(key, now);
      if (spokenText.current.size > 120) {
        spokenText.current = new Map(
          Array.from(spokenText.current.entries()).filter(([, at]) => now - at < 20000),
        );
      }
      void voice.speak(text);
    },
    [voice.speak],
  );

  const handleCommand = useCallback(
    async (text: string, source = 'text') => {
      if (!text.trim()) return;
      const data = await backend.sendCommand({ text: text.trim() }, source);
      if (!data) return;
      // "remember this" with no detail attached: the planner asks instead of
      // storing the word "this", and the capture box opens so the answer has a
      // place to go whether it is spoken or typed.
      if (data.intent === 'memory_capture') setRememberOpen(true);
      // Replies were read aloud only for voice-origin commands, so a typed
      // question produced text with no audio even with Speak Replies enabled.
      if (data.type === 'reply' && data.reply) {
        if (data.message?.id) spokenIds.current.add(data.message.id);
        speakOnce(data.reply);
      }
    },
    [backend.sendCommand, speakOnce],
  );
  handleCommandRef.current = (text, source) => {
    void handleCommand(text, source);
  };

  // Task results arrive later over the socket. Speak any assistant message that
  // is new since load, and never the same one twice.
  useEffect(() => {
    if (backend.messages.length === 0) return;
    if (!hydratedRef.current) {
      for (const message of backend.messages) spokenIds.current.add(message.id);
      hydratedRef.current = true;
      return;
    }
    const last = backend.messages[backend.messages.length - 1];
    if (!last || last.role !== 'assistant' || spokenIds.current.has(last.id)) return;
    spokenIds.current.add(last.id);
    speakOnce(last.content);
    if (spokenIds.current.size > 300) {
      spokenIds.current = new Set(Array.from(spokenIds.current).slice(-100));
    }
  }, [backend.messages, speakOnce]);

  useEffect(() => {
    let completed: string | null = null;
    let failed: { text: string; error: string | null } | null = null;
    for (const task of backend.tasks) {
      const before = previousStatuses.current.get(task.id);
      if (before && before !== task.status) {
        if (task.status === 'completed') completed = task.text;
        if (task.status === 'failed') failed = { text: task.text, error: task.error };
      }
      previousStatuses.current.set(task.id, task.status);
    }
    if (completed || failed) {
      if (flashTimer.current !== null) window.clearTimeout(flashTimer.current);
      const nextState: ReactorState = failed ? 'error' : 'completed';
      setFlash(nextState);
      flashTimer.current = window.setTimeout(() => setFlash(null), failed ? 4000 : 2200);
      if (failed) {
        setAlert({
          tone: 'danger',
          title: 'Task failed',
          body: `${failed.text} — ${failed.error ?? 'see task center for details'}`,
        });
      }
    }
  }, [backend.tasks]);

  useEffect(() => {
    if (!backend.dueReminder) return;
    const reminder = backend.dueReminder;
    backend.clearDueReminder();
    const label = reminder.late
      ? `Reminder: ${reminder.text} — due ${reminder.due_at}, ${reminder.late_minutes} minutes late`
      : `Reminder: ${reminder.text}`;
    setAlert({ tone: 'info', title: 'Reminder', body: label });
    // The scheduler only fires while the backend runs; say it if the user asked for speech.
    if (reminder.speak) void voice.speak(reminder.text);
  }, [backend.dueReminder, backend.clearDueReminder, voice.speak]);

  useEffect(() => {
    if (!alert) return;
    const timer = window.setTimeout(() => setAlert(null), 9000);
    return () => window.clearTimeout(timer);
  }, [alert]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.altKey && !event.ctrlKey && !event.metaKey) {
        const index = Number(event.key);
        if (index >= 1 && index <= TABS.length) {
          event.preventDefault();
          setTab(TABS[index - 1].id);
        }
      }
      if (event.key === 'Escape') {
        voice.stopSpeaking();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [voice.stopSpeaking]);

  const online = backend.connection === 'online';
  const wakeEnabled = settings['voice.wake_word_enabled'] === true;
  const autoListen = settings['voice.auto_listen'] === true;
  const wakeAppliedRef = useRef<boolean | null>(null);
  const autoListenAppliedRef = useRef(false);

  useEffect(() => {
    if (!online || wakeAppliedRef.current === wakeEnabled) return;
    wakeAppliedRef.current = wakeEnabled;
    if (wakeEnabled && !voice.wakeActive) void voice.toggleWake();
    if (!wakeEnabled && voice.wakeActive) void voice.toggleWake();
  }, [online, wakeEnabled, voice]);

  // voice.auto_listen was a visible checkbox that nothing ever read.
  useEffect(() => {
    if (!online || autoListenAppliedRef.current) return;
    if (!autoListen || wakeEnabled) {
      if (!autoListen) autoListenAppliedRef.current = true;
      return;
    }
    autoListenAppliedRef.current = true;
    void voice.start();
  }, [online, autoListen, wakeEnabled, voice]);

  const toggleWake = useCallback(async () => {
    const next = !voice.wakeActive;
    wakeAppliedRef.current = next;
    await backend.updateSettings({ 'voice.wake_word_enabled': next });
    if (next && !voice.wakeActive) void voice.toggleWake();
    if (!next && voice.wakeActive) void voice.toggleWake();
  }, [backend.updateSettings, voice]);

  const activeTask = useMemo(
    () =>
      backend.tasks.find((task) => task.status === 'running') ??
      backend.tasks.find((task) => ['pending', 'awaiting_confirmation'].includes(task.status)) ??
      null,
    [backend.tasks],
  );
  const lastAssistant = useMemo(() => {
    for (let i = backend.messages.length - 1; i >= 0; i -= 1) {
      if (backend.messages[i].role === 'assistant') return backend.messages[i].content;
    }
    return null;
  }, [backend.messages]);

  let reactorState: ReactorState = 'idle';
  if (!online) reactorState = 'offline';
  else if (backend.pending) reactorState = 'permission';
  else if (backend.error) reactorState = 'error';
  else if (activeTask?.status === 'running' || activeTask?.status === 'pending')
    reactorState = 'executing';
  else if (voice.speaking) reactorState = 'speaking';
  else if (voice.listening || voice.wakeActive) reactorState = 'listening';
  else if (backend.busy) reactorState = 'thinking';
  else if (flash) reactorState = flash;

  const alertBanner = alert
    ? { tone: alert.tone, title: alert.title, body: alert.body, dismiss: () => setAlert(null) }
    : backend.error
      ? {
          tone: 'danger' as const,
          title: 'Command failed',
          body: backend.error,
          dismiss: backend.dismissError,
        }
      : null;

  return (
    <div
      className="app"
      style={{ '--accent': accent } as CSSProperties}
      data-theme={themeName}
      data-motion={reducedMotion ? 'reduced' : 'full'}
      data-reactor={reactorState}
      data-tab={tab}
    >
      <aside className="rail" aria-label="Primary navigation">
        <button
          type="button"
          className="rail__brand"
          onClick={() => setTab('dashboard')}
          title="JARVIS dashboard"
        >
          <span className="rail__mark">J</span>
          <span className="rail__brand-text">
            <strong>J.A.R.V.I.S.</strong>
            <small>Personal AI</small>
          </span>
        </button>

        <nav className="rail__nav">
          {TABS.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`rail__item${tab === item.id ? ' is-active' : ''}`}
              onClick={() => setTab(item.id)}
              title={`${item.label} · ${item.hint} (Alt+${TABS.indexOf(item) + 1})`}
              aria-current={tab === item.id ? 'page' : undefined}
            >
              {item.icon}
              <span>{item.label}</span>
            </button>
          ))}
        </nav>

        <div className="rail__footer">
          <span className={`status-dot status-dot--${online ? 'good' : 'bad'}`} />
          <div>
            <strong>{online ? 'Backend linked' : 'Offline'}</strong>
            <small>v{backend.health?.version ?? '—'}</small>
          </div>
        </div>
      </aside>

      <div className="shell">
        <header className="topbar">
          <div className="topbar__title">
            <h1>{TAB_TITLES[tab]}</h1>
            <p>{TABS.find((item) => item.id === tab)?.hint}</p>
          </div>
          <div className="topbar__status">
            <span className={`pill pill--${online ? 'good' : 'bad'}`}>
              {backend.health?.active_provider ?? 'rules engine'}
            </span>
            <button
              type="button"
              className={`pill pill--${backend.phone?.connected ? 'good' : 'idle'}`}
              onClick={() => setTab('settings')}
              title="Phone bridge"
            >
              <IconPhone size={13} />
              {backend.phone?.connected ? `${backend.phone.battery ?? '—'}%` : 'Phone'}
            </button>
            <button
              type="button"
              className={`pill pill--${voice.listening ? 'live' : voice.wakeActive ? 'warn' : 'idle'}`}
              onClick={voice.toggle}
              disabled={!online || !voice.supported}
              title={voice.listening ? 'Stop listening' : 'Start listening'}
            >
              {voice.listening || voice.wakeActive ? <IconMic size={13} /> : <IconMicOff size={13} />}
              {voice.listening ? 'Mic live' : voice.wakeActive ? 'Wake word' : 'Mic off'}
            </button>
            <button
              type="button"
              className="pill pill--idle"
              onClick={() => {
                setTab('command');
                void backend.markNotificationsRead();
              }}
              title="Notifications"
            >
              <IconBell size={13} />
              {backend.unread > 0 ? backend.unread : 'Alerts'}
            </button>
            <Clock />
          </div>
        </header>

        <main className="content">
          {alertBanner && (
            <PermissionBanner
              tone={alertBanner.tone}
              title={alertBanner.title}
              onDismiss={alertBanner.dismiss}
            >
              {alertBanner.body}
            </PermissionBanner>
          )}

          {tab === 'dashboard' && (
            <DashboardPage
              backend={backend}
              voice={voice}
              reactorState={reactorState}
              reducedMotion={reducedMotion}
              sections={HOME_SECTIONS}
              onNavigate={setTab}
              wakeEnabled={wakeEnabled}
              onToggleWake={() => void toggleWake()}
              onRemember={() => setRememberOpen(true)}
            />
          )}
          {tab === 'command' && (
            <CommandPage
              backend={backend}
              voice={voice}
              stats={stats}
              statsError={statsError}
              wakeEnabled={wakeEnabled}
              onToggleWake={() => void toggleWake()}
              onSend={(text, source) => void handleCommand(text, source)}
              onSpeak={(text) => void voice.speak(text)}
              onRefreshStats={() => void refreshStats()}
              onNavigate={setTab}
            />
          )}
          {tab === 'monitor' && (
            <MonitorPage
              stats={stats}
              health={backend.health}
              statsError={statsError}
              online={online}
              backend={backend}
              onRefreshStats={() => void refreshStats()}
            />
          )}
          {tab === 'chat' && (
            <ChatPage
              backend={backend}
              voice={voice}
              onSend={(text, source) => void handleCommand(text, source)}
              onSpeak={(text) => void voice.speak(text)}
            />
          )}
          {tab === 'phone' && <PhonePage backend={backend} onOpenSettings={() => setTab('settings')} />}
          {tab === 'automation' && <AutomationPage backend={backend} />}
          {tab === 'memory' && <MemoryPage />}
          {tab === 'settings' && <SettingsPage backend={backend} voice={voice} />}
        </main>
      </div>

      <FloatingOrb
        state={reactorState}
        online={online}
        listening={voice.listening}
        micSupported={voice.supported}
        busy={backend.busy}
        lastMessage={lastAssistant}
        activeTask={activeTask}
        onSubmit={(text) => void handleCommand(text, 'text')}
        onToggleMic={voice.toggle}
        onOpenDashboard={() => setTab('command')}
      />

      {rememberOpen && (
        <RememberCapture
          online={online}
          onClose={() => setRememberOpen(false)}
          onSaved={(summary) => setAlert({ tone: 'info', title: 'Remembered', body: summary })}
        />
      )}

      {backend.pending && (
        <ConfirmDialog
          confirmation={backend.pending}
          busy={backend.busy}
          onConfirm={() => void backend.confirmPending()}
          onCancel={() => void backend.dismissPending()}
        />
      )}
    </div>
  );
}
