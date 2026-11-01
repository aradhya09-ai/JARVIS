import { useCallback, useEffect, useState } from 'react';

import { api } from '../services/api';
import type {
  DeviceRecord,
  Health,
  IntegrationRecord,
  PairingInfo,
  SettingsMap,
} from '../types';
import { IconPhone, IconRefresh, IconShield, IconTrash, IconWarning } from './Icons';

interface SettingsPanelProps {
  settings: SettingsMap;
  health: Health | null;
  online: boolean;
  voices: SpeechSynthesisVoice[];
  testVoice: (sample: string) => void;
  update: (values: SettingsMap) => Promise<{ settings: SettingsMap; restart_required: string[] } | null>;
  onClearConversation: () => void;
  onNotice: (message: string) => void;
}

const LANGUAGE_OPTIONS = [
  { value: 'en-IN', label: 'English (India)' },
  { value: 'en-US', label: 'English (United States)' },
  { value: 'en-GB', label: 'English (United Kingdom)' },
  { value: 'hi-IN', label: 'Hindi (India)' },
];

const VOICE_SAMPLE = 'Namaste Aradhya, main JARVIS hoon.';

interface SapiVoiceInfo {
  voices: { name: string; culture: string }[];
  hindi_available: boolean;
  hindi_voices: string[];
  note: string;
}

export function SettingsPanel({
  settings,
  health,
  online,
  voices,
  testVoice,
  update,
  onClearConversation,
  onNotice,
}: SettingsPanelProps) {
  const str = (key: string, fallback = '') =>
    typeof settings[key] === 'string' ? (settings[key] as string) : fallback;
  const bool = (key: string, fallback = false) =>
    typeof settings[key] === 'boolean' ? (settings[key] as boolean) : fallback;
  const num = (key: string, fallback = 0) =>
    typeof settings[key] === 'number' ? (settings[key] as number) : fallback;

  const [saving, setSaving] = useState<string | null>(null);
  const [devices, setDevices] = useState<DeviceRecord[]>([]);
  const [pairing, setPairing] = useState<{ info: PairingInfo; expiresAt: number } | null>(null);
  const [clock, setClock] = useState(() => Date.now());
  const [memoryCount, setMemoryCount] = useState<number | null>(null);
  const [integrations, setIntegrations] = useState<IntegrationRecord[]>([]);
  const [integrationNote, setIntegrationNote] = useState('');
  const [sapi, setSapi] = useState<SapiVoiceInfo | null>(null);
  const [testing, setTesting] = useState(false);
  const [ollamaModel, setOllamaModel] = useState(str('ai.ollama_model', 'qwen2.5:3b'));
  const [outputDir, setOutputDir] = useState(str('output.directory'));
  const [projectsDir, setProjectsDir] = useState(str('projects.directory'));
  const [temperature, setTemperature] = useState(num('ai.temperature', 0.2));
  const [accent, setAccent] = useState(str('theme.accent', '#ff8a1e'));

  useEffect(() => {
    setOllamaModel(str('ai.ollama_model', 'qwen2.5:3b'));
    setOutputDir(str('output.directory'));
    setProjectsDir(str('projects.directory'));
    setTemperature(num('ai.temperature', 0.2));
    setAccent(str('theme.accent', '#ff8a1e'));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings]);

  const loadDevices = useCallback(async () => {
    try {
      const data = await api.devices();
      setDevices(data.devices);
    } catch {
      /* offline */
    }
  }, []);

  const loadIntegrations = useCallback(async () => {
    try {
      const data = await api.integrations();
      setIntegrations(data.integrations);
      setIntegrationNote(data.note);
    } catch {
      /* offline */
    }
  }, []);

  const loadMemory = useCallback(async () => {
    try {
      const data = await api.memory();
      setMemoryCount(data.memories.length);
    } catch {
      setMemoryCount(null);
    }
  }, []);

  useEffect(() => {
    if (!online) return;
    void loadDevices();
    void loadIntegrations();
    void loadMemory();
  }, [online, loadDevices, loadIntegrations, loadMemory]);

  useEffect(() => {
    if (!pairing) return;
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [pairing]);

  const patch = async (key: string, value: unknown, label: string) => {
    setSaving(label);
    const result = await update({ [key]: value });
    setSaving(null);
    if (result && result.restart_required.length > 0) {
      onNotice(`Saved. Restart the backend to apply ${result.restart_required.join(', ')}.`);
    }
    return result;
  };

  const startPairing = async () => {
    try {
      const info = await api.pairStart();
      setPairing({ info, expiresAt: Date.now() + info.expires_in * 1000 });
      setClock(Date.now());
      onNotice('Pairing code generated. It expires in two minutes.');
    } catch (cause) {
      onNotice(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const revoke = async (device: DeviceRecord) => {
    if (!window.confirm(`Revoke ${device.name}? The phone will need to pair again.`)) return;
    try {
      await api.revokeDevice(device.id);
      onNotice(`Revoked ${device.name}.`);
      await loadDevices();
    } catch (cause) {
      onNotice(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const clearConversation = async () => {
    if (!window.confirm('Delete the local conversation history? This cannot be undone.')) return;
    onClearConversation();
  };

  const clearMemory = async () => {
    if (!window.confirm('Delete every memory JARVIS stored? This cannot be undone.')) return;
    try {
      await api.clearMemory();
      setMemoryCount(0);
      onNotice('Stored memories cleared.');
    } catch (cause) {
      onNotice(cause instanceof Error ? cause.message : String(cause));
    }
  };

  // The browser voice used to live only in localStorage, so it was invisible to
  // the rest of the app and did not survive another device. Keep the old value
  // by migrating it into the persisted setting once.
  const chooseVoice = (value: string) => {
    const stored = window.localStorage.getItem('jarvis.browserVoice');
    if (!str('voice.voice_name') && stored && value === stored) {
      window.localStorage.removeItem('jarvis.browserVoice');
    }
    void patch('voice.voice_name', value, 'browser-voice');
  };

  useEffect(() => {
    if (!online) return;
    let cancelled = false;
    void api
      .sapiVoices()
      .then((data) => {
        if (!cancelled) {
          setSapi({
            voices: data.voices,
            hindi_available: data.hindi_available,
            hindi_voices: data.hindi_voices,
            note: data.note,
          });
        }
      })
      .catch(() => {
        if (!cancelled) setSapi(null);
      });
    return () => {
      cancelled = true;
    };
  }, [online]);

  const runVoiceTest = () => {
    setTesting(true);
    testVoice(VOICE_SAMPLE);
    window.setTimeout(() => setTesting(false), 1200);
  };

  const remaining = pairing ? Math.max(0, Math.floor((pairing.expiresAt - clock) / 1000)) : 0;
  const codeExpired = pairing !== null && remaining <= 0;

  return (
    <div className="settings">
      <section className="panel">
        <header className="panel__header">
          <div>
            <h2 className="panel__title">AI model</h2>
            <p className="panel__hint">
              Deterministic commands work without a model. The model only handles open-ended language.
            </p>
          </div>
        </header>
        <div className="form-grid">
          <label className="field">
            <span>Provider preference</span>
            <select
              value={str('ai.provider', 'auto')}
              onChange={(event) => void patch('ai.provider', event.target.value, 'provider')}
              disabled={!online}
            >
              <option value="auto">Automatic (local first)</option>
              <option value="ollama">Ollama only</option>
              <option value="cloud">Cloud only</option>
            </select>
            <small>Cloud requires JARVIS_CLOUD_* variables in .env.</small>
          </label>
          <label className="field">
            <span>Ollama model tag</span>
            <div className="field__row">
              <input
                value={ollamaModel}
                onChange={(event) => setOllamaModel(event.target.value)}
                placeholder="qwen2.5:3b"
              />
              <button
                type="button"
                className="button button--tiny"
                disabled={!online || saving === 'model'}
                onClick={() => void patch('ai.ollama_model', ollamaModel.trim(), 'model')}
              >
                Save
              </button>
            </div>
            <small>Pull it first with: ollama pull {ollamaModel || 'qwen2.5:3b'}</small>
          </label>
          <label className="field">
            <span>Temperature · {temperature.toFixed(2)}</span>
            <input
              type="range"
              min={0}
              max={1.5}
              step={0.05}
              value={temperature}
              onChange={(event) => setTemperature(Number(event.target.value))}
              onPointerUp={() => void patch('ai.temperature', temperature, 'temperature')}
              onKeyUp={() => void patch('ai.temperature', temperature, 'temperature')}
            />
            <small>Lower is more deterministic; higher is more creative.</small>
          </label>
        </div>
        <div className="provider-list">
          {(health?.providers ?? []).map((provider) => (
            <div key={provider.name} className="provider">
              <span className={`status-dot status-dot--${provider.available ? 'good' : 'bad'}`} />
              <div>
                <strong>{provider.name}</strong>
                <p>{provider.detail}</p>
              </div>
              <span className="provider__model">{provider.model ?? '—'}</span>
            </div>
          ))}
          {!health && <p className="empty-note">Provider status unavailable while offline.</p>}
        </div>
      </section>

      <section className="panel">
        <header className="panel__header">
          <div>
            <h2 className="panel__title">Voice</h2>
            <p className="panel__hint">
              Speech recognition runs in the browser; audio is only captured while listening is active.
            </p>
          </div>
          <span className="chip chip--info">
            {health?.voice.backend_tts.available ? 'Local TTS ready' : 'Browser TTS'}
          </span>
        </header>
        <div className="form-grid">
          <label className="field field--check">
            <input
              type="checkbox"
              checked={bool('voice.tts_enabled', true)}
              onChange={(event) => void patch('voice.tts_enabled', event.target.checked, 'tts')}
              disabled={!online}
            />
            <div>
              <span>Speak replies aloud</span>
              <small>Uses local Windows SAPI when available, otherwise the browser voice.</small>
            </div>
          </label>
          <label className="field">
            <span>Browser voice</span>
            <select
              value={str('voice.voice_name')}
              onChange={(event) => chooseVoice(event.target.value)}
            >
              <option value="">Match the language automatically</option>
              {voices.map((voice) => (
                <option key={voice.name} value={voice.name}>
                  {voice.name} ({voice.lang})
                </option>
              ))}
            </select>
            <small>
              {voices.length === 0
                ? 'No browser voices reported yet. They usually appear a moment after load.'
                : `Used when Windows speech is unavailable. ${voices.length} voice(s) available.`}
            </small>
          </label>
          <label className="field">
            <span>Windows speech voice</span>
            <select
              value={str('voice.sapi_voice')}
              onChange={(event) => void patch('voice.sapi_voice', event.target.value, 'sapi-voice')}
              disabled={!online || !health?.voice.backend_tts.available}
            >
              <option value="">Engine default (English)</option>
              {(sapi?.voices ?? []).map((voice) => (
                <option key={voice.name} value={voice.name}>
                  {voice.name} ({voice.culture})
                </option>
              ))}
            </select>
            <small>
              {sapi === null
                ? 'Could not read the installed Windows voices.'
                : sapi.note}
            </small>
          </label>
          <div className="field">
            <span>Test the selected voice</span>
            <div className="field__row">
              <button
                type="button"
                className="button button--tiny"
                onClick={runVoiceTest}
                disabled={testing}
              >
                {testing ? 'Speaking…' : `Speak: “${VOICE_SAMPLE}”`}
              </button>
            </div>
            <small>
              Selected: {str('voice.voice_name') || 'automatic'} · language{' '}
              {str('voice.language', 'en-IN')}
              {sapi && !sapi.hindi_available ? ' · no Hindi Windows voice installed' : ''}
            </small>
          </div>
          <label className="field">
            <span>Recognition language</span>
            <select
              value={str('voice.language', 'en-IN')}
              onChange={(event) => void patch('voice.language', event.target.value, 'language')}
              disabled={!online}
            >
              {LANGUAGE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            {str('voice.language', 'en-IN').startsWith('hi') && sapi && !sapi.hindi_available && (
              <small>
                Hindi replies will be shown in Devanagari and spoken by the English Windows voice
                using Latin phonetics, so the pronunciation is approximate, not native. Installing a
                Hindi voice removes this limitation.
              </small>
            )}
          </label>
          <label className="field field--check">
            <input
              type="checkbox"
              checked={bool('voice.wake_word_enabled', false)}
              onChange={(event) =>
                void patch('voice.wake_word_enabled', event.target.checked, 'wake')
              }
              disabled={!online}
            />
            <div>
              <span>Enable wake word</span>
              <small>
                Matches the configured phrase with the browser speech recogniser — it is not a
                dedicated “Hey JARVIS” acoustic model. It only works while this dashboard is open,
                the microphone is permitted, and the browser is online for recognition. Background
                speech that is not a wake phrase or a command is never sent to the AI.
              </small>
            </div>
          </label>
          <label className="field">
            <span>Wake phrase</span>
            <input
              defaultValue={str('voice.wake_word', 'hey jarvis')}
              onBlur={(event) => {
                const value = event.target.value.trim().toLowerCase();
                if (value && value !== str('voice.wake_word')) {
                  void patch('voice.wake_word', value, 'wake-phrase');
                }
              }}
              maxLength={40}
            />
          </label>
          <label className="field field--check">
            <input
              type="checkbox"
              checked={bool('voice.auto_listen', false)}
              onChange={(event) => void patch('voice.auto_listen', event.target.checked, 'auto')}
              disabled={!online}
            />
            <div>
              <span>Start listening on dashboard load</span>
              <small>Browsers still require a click before the microphone may be used.</small>
            </div>
          </label>
        </div>
        <div className="voice-status">
          <div className="kv">
            <span>Backend STT (faster-whisper)</span>
            <strong>{health?.voice.backend_stt.available ? 'Available' : 'Not installed'}</strong>
          </div>
          <div className="kv">
            <span>Backend TTS (Windows SAPI)</span>
            <strong>{health?.voice.backend_tts.available ? 'Available' : 'Unavailable'}</strong>
          </div>
          <div className="kv">
            <span>Hindi speech voice</span>
            <strong>
              {sapi === null
                ? 'Unknown'
                : sapi.hindi_available
                  ? `Installed: ${sapi.hindi_voices?.join(', ') ?? 'yes'}`
                  : 'Not installed'}
            </strong>
          </div>
          <p>{health?.voice.backend_stt.detail ?? 'Voice status unavailable while offline.'}</p>
          <p>
            {health?.voice.backend_tts.detail ?? ''}{' '}
            {sapi && !sapi.hindi_available
              ? 'Hindi replies are shown in Devanagari but are read aloud by an English voice using Latin phonetics.'
              : ''}
          </p>
        </div>
      </section>

      <section className="panel">
        <header className="panel__header">
          <div>
            <h2 className="panel__title">Permissions &amp; safety</h2>
            <p className="panel__hint">The model can never approve its own actions.</p>
          </div>
          <IconShield size={18} />
        </header>
        <div className="form-grid">
          <label className="field field--check">
            <input
              type="checkbox"
              checked={bool('permissions.auto_confirm_moderate', false)}
              onChange={(event) =>
                void patch(
                  'permissions.auto_confirm_moderate',
                  event.target.checked,
                  'auto-confirm',
                )
              }
              disabled={!online}
            />
            <div>
              <span>Auto-confirm moderate actions</span>
              <small>High-risk actions always require explicit confirmation.</small>
            </div>
          </label>
          <label className="field field--check">
            <input
              type="checkbox"
              checked={bool('privacy.task_history', true)}
              onChange={(event) => void patch('privacy.task_history', event.target.checked, 'history')}
              disabled={!online}
            />
            <div>
              <span>Keep task history</span>
              <small>When off, finished tasks are removed from the local database.</small>
            </div>
          </label>
          <label className="field field--check">
            <input
              type="checkbox"
              checked={bool('privacy.memory_enabled', true)}
              onChange={(event) => void patch('privacy.memory_enabled', event.target.checked, 'memory')}
              disabled={!online}
            />
            <div>
              <span>Allow remembered preferences</span>
              <small>Stored locally in SQLite; no telemetry leaves the PC.</small>
            </div>
          </label>
        </div>
        <h3 className="subhead">Filesystem boundaries</h3>
        <div className="path-list">
          <div>
            <span>Read</span>
            <ul>
              {(health?.permissions.read ?? []).map((path) => (
                <li key={path}>
                  <code>{path}</code>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <span>Write</span>
            <ul>
              {(health?.permissions.write ?? []).map((path) => (
                <li key={path}>
                  <code>{path}</code>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </section>

      <section className="panel">
        <header className="panel__header">
          <div>
            <h2 className="panel__title">Monitoring &amp; notifications</h2>
            <p className="panel__hint">Telemetry is polled from Windows only.</p>
          </div>
        </header>
        <div className="form-grid">
          <label className="field field--check">
            <input
              type="checkbox"
              checked={bool('monitor.enabled', true)}
              onChange={(event) => void patch('monitor.enabled', event.target.checked, 'monitor')}
              disabled={!online}
            />
            <div>
              <span>Live system monitoring</span>
              <small>Updates the dashboard with real CPU, memory, battery and disk data.</small>
            </div>
          </label>
          <label className="field">
            <span>Refresh interval</span>
            <select
              value={num('monitor.interval_ms', 2000)}
              onChange={(event) =>
                void patch('monitor.interval_ms', Number(event.target.value), 'interval')
              }
              disabled={!online}
            >
              <option value={1000}>1 second</option>
              <option value={2000}>2 seconds</option>
              <option value={5000}>5 seconds</option>
              <option value={10000}>10 seconds</option>
            </select>
          </label>
          <label className="field field--check">
            <input
              type="checkbox"
              checked={bool('notifications.enabled', true)}
              onChange={(event) =>
                void patch('notifications.enabled', event.target.checked, 'notifications')
              }
              disabled={!online}
            />
            <div>
              <span>Desktop notifications</span>
              <small>Shown inside the dashboard. Phone notifications require a paired device.</small>
            </div>
          </label>
        </div>
      </section>

      <section className="panel">
        <header className="panel__header">
          <div>
            <h2 className="panel__title">Appearance</h2>
            <p className="panel__hint">Theme changes apply instantly across the HUD.</p>
          </div>
        </header>
        <div className="form-grid">
          <label className="field">
            <span>Theme</span>
            <div className="field__row">
              {(['blue', 'amber'] as const).map((name) => (
                <button
                  key={name}
                  type="button"
                  className={`button button--tiny${str('theme.name', 'blue') === name ? ' is-active' : ''}`}
                  onClick={() => void patch('theme.name', name, 'theme')}
                  disabled={!online}
                >
                  {name === 'blue' ? 'Electric blue' : 'Arc amber'}
                </button>
              ))}
            </div>
            <small>Electric blue &amp; cyan is the default HUD theme; amber switches the whole palette.</small>
          </label>
          <label className="field">
            <span>Accent colour</span>
            <div className="field__row">
              <input
                type="color"
                value={accent}
                onChange={(event) => setAccent(event.target.value)}
                onBlur={() => void patch('theme.accent', accent, 'accent')}
              />
              <code>{accent}</code>
            </div>
            <small>Used by the heads-up display, rings and highlights.</small>
          </label>
          <label className="field field--check">
            <input
              type="checkbox"
              checked={bool('theme.reduced_motion', false)}
              onChange={(event) => void patch('theme.reduced_motion', event.target.checked, 'motion')}
              disabled={!online}
            />
            <div>
              <span>Reduce motion</span>
              <small>Slows the heads-up display dials and disables decorative animation where practical.</small>
            </div>
          </label>
        </div>
      </section>

      <section className="panel">
        <header className="panel__header">
          <div>
            <h2 className="panel__title">
              <IconPhone size={15} /> Phone pairing
            </h2>
            <p className="panel__hint">One-time code · tokens are stored hashed · devices can be revoked.</p>
          </div>
          <button type="button" className="button button--tiny" onClick={() => void startPairing()}>
            Generate code
          </button>
        </header>

        <div className="form-grid">
          <label className="field">
            <span>Output directory</span>
            <div className="field__row">
              <input value={outputDir} onChange={(event) => setOutputDir(event.target.value)} />
              <button
                type="button"
                className="button button--tiny"
                disabled={!online || outputDir.trim() === str('output.directory')}
                onClick={() => void patch('output.directory', outputDir.trim(), 'output')}
              >
                Save
              </button>
            </div>
            <small>Presentations and generated images are written here (restart required).</small>
          </label>
          <label className="field">
            <span>Projects directory</span>
            <div className="field__row">
              <input
                value={projectsDir}
                onChange={(event) => setProjectsDir(event.target.value)}
              />
              <button
                type="button"
                className="button button--tiny"
                disabled={!online || projectsDir.trim() === str('projects.directory')}
                onClick={() => void patch('projects.directory', projectsDir.trim(), 'projects')}
              >
                Save
              </button>
            </div>
            <small>Generated code projects stay inside an approved folder (restart required).</small>
          </label>
        </div>

        {pairing && (
          <div className="pairing">
            <div className="pairing__code">
              <span>Pairing code</span>
              <strong>{codeExpired ? 'EXPIRED' : pairing.info.code}</strong>
              <small>
                {codeExpired ? 'Generate a new code.' : `Expires in ${remaining}s`}
              </small>
            </div>
            <div className="pairing__details">
              <p>
                <span>USB / ADB URL</span>
                <code>{pairing.info.ws_url_loopback}</code>
              </p>
              <p>
                <span>Wi-Fi URL</span>
                <code>{pairing.info.ws_url_lan}</code>
              </p>
              <p className="pairing__note">{pairing.info.note}</p>
              <button
                type="button"
                className="button button--tiny button--ghost"
                onClick={() => {
                  void navigator.clipboard
                    .writeText(pairing.info.ws_url_lan)
                    .then(() => onNotice('LAN address copied.'))
                    .catch(() => onNotice('Copy failed — select the URL manually.'));
                }}
              >
                Copy Wi-Fi URL
              </button>
            </div>
          </div>
        )}

        <h3 className="subhead">Paired devices</h3>
        <div className="device-list">
          {devices.length === 0 && <p className="empty-note">No devices paired yet.</p>}
          {devices.map((device) => (
            <div key={device.id} className="device">
              <div>
                <strong>{device.name}</strong>
                <span>
                  {device.revoked ? 'Revoked' : 'Active'} · paired{' '}
                  {new Date(device.paired_at).toLocaleDateString()} · last seen{' '}
                  {device.last_seen ? new Date(device.last_seen).toLocaleTimeString() : 'never'}
                </span>
              </div>
              {!device.revoked && (
                <button
                  type="button"
                  className="button button--tiny button--danger"
                  onClick={() => void revoke(device)}
                >
                  Revoke
                </button>
              )}
            </div>
          ))}
        </div>
        <button
          type="button"
          className="icon-button"
          onClick={() => void loadDevices()}
          title="Refresh devices"
        >
          <IconRefresh size={15} />
        </button>
      </section>

      <section className="panel">
        <header className="panel__header">
          <div>
            <h2 className="panel__title">Integrations</h2>
            <p className="panel__hint">Local configuration is stored; OAuth is not implemented.</p>
          </div>
          <IconWarning size={17} />
        </header>
        <div className="integration-list">
          {['gmail', 'outlook', 'google_calendar', 'microsoft_calendar', 'moodle', 'github'].map(
            (provider) => {
              const record = integrations.find((item) => item.provider === provider);
              return (
                <div key={provider} className="integration">
                  <strong>{provider.replace('_', ' ')}</strong>
                  <span className="chip chip--warning">NOT IMPLEMENTED</span>
                  <span>{record?.enabled ? 'Configured locally' : 'Not configured'}</span>
                </div>
              );
            },
          )}
        </div>
        <p className="note">{integrationNote || 'OAuth authorisation is not available in this build.'}</p>
      </section>

      <section className="panel">
        <header className="panel__header">
          <div>
            <h2 className="panel__title">Privacy &amp; data</h2>
            <p className="panel__hint">
              Memories: {memoryCount === null ? 'unavailable' : memoryCount}. Everything stays on this PC.
            </p>
          </div>
        </header>
        <div className="form-grid">
          <div className="field">
            <span>Conversation history</span>
            <button type="button" className="button button--tiny button--danger" onClick={() => void clearConversation()}>
              <IconTrash size={13} /> Clear conversation
            </button>
          </div>
          <div className="field">
            <span>Stored preferences</span>
            <button type="button" className="button button--tiny button--danger" onClick={() => void clearMemory()}>
              <IconTrash size={13} /> Clear memories
            </button>
          </div>
        </div>
        <p className="note">
          The dashboard never records audio on its own. The microphone is released as soon as
          listening stops, and alerting states are always visible on the heads-up display.
        </p>
      </section>

      <section className="panel panel--about">
        <div>
          <h2 className="panel__title">J.A.R.V.I.S.</h2>
          <p className="panel__hint">
            Version {health?.version ?? '—'} · {health?.tools ?? 0} tools registered · database schema{' '}
            {health?.database.schema ?? '—'}
          </p>
        </div>
        <div className="kv">
          <span>Backend</span>
          <strong>{health ? `${health.bound_host} · ${health.status}` : 'Offline'}</strong>
        </div>
        <div className="kv">
          <span>Active model</span>
          <strong>{health?.active_provider ?? 'None (deterministic rules only)'}</strong>
        </div>
      </section>
    </div>
  );
}
