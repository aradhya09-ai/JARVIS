import type {
  AppEntry,
  AuditEntry,
  CommandResponse,
  DeviceRecord,
  DueReminder,
  Health,
  ImportCandidate,
  ImportConversation,
  IntegrationRecord,
  JarvisEvent,
  KnowledgeItem,
  Notification,
  PairingInfo,
  ProcessInfo,
  ReminderRecord,
  RunningProcess,
  SettingsMap,
  SiteAlias,
  SnapshotPayload,
  SystemStats,
  Task,
  TaskEvent,
  WindowInfo,
} from '../types';

const FALLBACK_BASE = 'http://127.0.0.1:8765';

function resolveBase(): string {
  const configured = import.meta.env.VITE_JARVIS_URL?.trim().replace(/\/$/, '');
  if (configured) return configured;
  if (typeof window !== 'undefined' && window.location.port === '8765') {
    return window.location.origin;
  }
  return FALLBACK_BASE;
}

export const API_BASE = resolveBase();
export const WS_BASE = API_BASE.replace(/^http/, 'ws');

export interface TTSInfo {
  requested_language?: string;
  devanagari?: boolean;
  romanized?: boolean;
  voice?: string;
}

export interface SystemSpeakResult extends TTSInfo {
  duration_ms: number;
  engine: string;
}

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function parseError(response: Response): Promise<never> {
  let detail = `${response.status} ${response.statusText}`;
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === 'string') detail = body.detail;
    else if (body.detail) detail = JSON.stringify(body.detail);
  } catch {
    /* non-JSON error body */
  }
  throw new ApiError(detail, response.status);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      'X-JARVIS-Client': 'desktop',
      ...(init?.headers ?? {}),
    },
    cache: 'no-store',
  });
  if (!response.ok) return parseError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  health: () => request<Health>('/api/health'),

  stats: () => request<SystemStats>('/api/system/stats'),
  statsWithProcesses: () => request<SystemStats>('/api/system/stats?processes=true'),
  processes: () =>
    request<{ processes: ProcessInfo[]; windows: WindowInfo[] }>('/api/system/processes'),

  apps: () => request<{ apps: AppEntry[]; favorites: string[] }>('/api/apps'),
  appsRunning: () =>
    request<{ windows: WindowInfo[]; processes: RunningProcess[] }>('/api/apps/running'),

  command: (payload: {
    text?: string;
    tool?: string;
    args?: Record<string, unknown>;
    source?: string;
  }) =>
    request<CommandResponse>('/api/command', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  tasks: (status?: string) =>
    request<{ tasks: Task[] }>(`/api/tasks${status ? `?status=${status}` : ''}`),
  task: (id: string) => request<{ task: Task; events: TaskEvent[] }>(`/api/tasks/${id}`),
  confirm: (id: string, token: string) =>
    request<{ task: Task }>(`/api/tasks/${id}/confirm`, {
      method: 'POST',
      body: JSON.stringify({ token }),
    }),
  cancel: (id: string) =>
    request<{ cancelled: boolean; task: Task }>(`/api/tasks/${id}/cancel`, { method: 'POST' }),

  messages: () => request<{ messages: import('../types').Message[] }>('/api/messages'),
  clearMessages: () => request<{ cleared: boolean }>('/api/messages', { method: 'DELETE' }),

  notifications: () => request<{ notifications: Notification[] }>('/api/notifications'),
  markNotificationsRead: () =>
    request<{ ok: boolean }>('/api/notifications/read', { method: 'POST' }),

  settings: () =>
    request<{
      settings: SettingsMap;
      restart_required_keys: string[];
      permissions: { read: string[]; write: string[] };
      version: string;
    }>('/api/settings'),
  patchSettings: (values: SettingsMap) =>
    request<{ settings: SettingsMap; restart_required: string[] }>('/api/settings', {
      method: 'PATCH',
      body: JSON.stringify({ values }),
    }),

  memory: () => request<{ memories: { key: string; value: unknown }[]; enabled: boolean }>('/api/memory'),
  deleteMemory: (key: string) =>
    request<{ ok: boolean }>(`/api/memory/${encodeURIComponent(key)}`, { method: 'DELETE' }),
  clearMemory: () => request<{ ok: boolean }>('/api/memory', { method: 'DELETE' }),

  hardware: () => request<import('../types').HardwareInfo>('/api/system/hardware'),

  integrations: () =>
    request<{ integrations: IntegrationRecord[]; note: string }>('/api/integrations'),

  permissions: () => request<{ read: string[]; write: string[] }>('/api/permissions'),
  providers: () =>
    request<{ providers: Health['providers']; active: string | null; image: string }>(
      '/api/providers',
    ),
  tools: () => request<{ tools: Record<string, unknown>[] }>('/api/tools'),
  audit: (limit = 100) => request<{ entries: AuditEntry[] }>(`/api/audit?limit=${limit}`),

  phoneStatus: () => request<import('../types').PhoneStatus>('/api/phone/status'),
  pairStart: () => request<PairingInfo>('/api/phone/pair/start', { method: 'POST' }),
  devices: () => request<{ devices: DeviceRecord[] }>('/api/phone/devices'),
  revokeDevice: (id: string) =>
    request<{ revoked: boolean; disconnected: boolean }>(
      `/api/phone/devices/${encodeURIComponent(id)}/revoke`,
      { method: 'POST' },
    ),
  notifyPhone: (title: string, body: string) =>
    request<{ delivered: number; connected: boolean }>('/api/phone/notify', {
      method: 'POST',
      body: JSON.stringify({ title, body }),
    }),

  voiceStatus: () =>
    request<{
      backend_stt: VoiceEngineInfoLike;
      backend_tts: VoiceEngineInfoLike;
      speaker_output: VoiceEngineInfoLike;
      wake_word: Record<string, unknown>;
      browser_note: string;
    }>('/api/voice/status'),

  async tts(
    text: string,
    language?: string,
    voice?: string,
  ): Promise<{ blob: Blob; info: TTSInfo }> {
    const response = await fetch(`${API_BASE}/api/voice/tts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-JARVIS-Client': 'desktop' },
      body: JSON.stringify({
        text,
        ...(language ? { language } : {}),
        ...(voice ? { voice } : {}),
      }),
      cache: 'no-store',
    });
    if (!response.ok) return parseError(response);
    let info: TTSInfo = {};
    try {
      info = JSON.parse(response.headers.get('X-Jarvis-TTS-Info') ?? '{}') as TTSInfo;
    } catch {
      /* the audio is still valid; only the explanation is missing */
    }
    return { blob: await response.blob(), info };
  },

  /**
   * Play a reply through the laptop speakers from the backend. Chrome will not
   * let an unclicked tab produce any sound, and a wake-word assistant has to
   * answer before it is ever clicked, so this is the route that keeps JARVIS
   * audible. The clip length comes back so the UI stops "speaking" when the
   * audio genuinely ends.
   */
  ttsSpeak: (text: string, language?: string, voice?: string) =>
    request<SystemSpeakResult>('/api/voice/tts/speak', {
      method: 'POST',
      body: JSON.stringify({
        text,
        ...(language ? { language } : {}),
        ...(voice ? { voice } : {}),
      }),
    }),

  ttsSpeakStop: () =>
    request<{ stopped: boolean }>('/api/voice/tts/speak/stop', { method: 'POST' }),

  sapiVoices: () =>
    request<{
      engine: string;
      voices: { name: string; culture: string }[];
      hindi_available: boolean;
      hindi_voices: string[];
      note: string;
    }>('/api/voice/voices'),

  // ---------------------------------------------------------- personal data
  knowledge: (category?: string) =>
    request<{
      categories: string[];
      sources: string[];
      items: KnowledgeItem[];
      counts: Record<string, number>;
      note: string;
    }>(`/api/knowledge${category ? `?category=${encodeURIComponent(category)}` : ''}`),
  addKnowledge: (payload: {
    key: string;
    value: unknown;
    category: string;
    source?: string;
    verified?: boolean;
    as_of?: string | null;
    note?: string | null;
  }) => request<{ item: KnowledgeItem }>('/api/knowledge', { method: 'POST', body: JSON.stringify(payload) }),
  uploadKnowledge: (payload: { filename: string; content: string; category: string }) =>
    request<{ item: KnowledgeItem; truncated: boolean }>('/api/knowledge/upload', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  patchKnowledge: (key: string, payload: Record<string, unknown>) =>
    request<{ item: KnowledgeItem }>(`/api/knowledge/${encodeURIComponent(key)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  deleteKnowledge: (key: string) =>
    request<{ deleted: boolean }>(`/api/knowledge/${encodeURIComponent(key)}`, { method: 'DELETE' }),

  // ------------------------------------------------------ ChatGPT export import
  previewImport: (raw: string) =>
    request<{
      import_id: string;
      conversations: ImportConversation[];
      candidates: ImportCandidate[];
      notes: string[];
      expires_in: number;
    }>('/api/import/chatgpt/preview', { method: 'POST', body: JSON.stringify({ raw }) }),
  approveImport: (payload: { import_id: string; fact_ids: string[]; excluded?: string[] }) =>
    request<{
      stored: number;
      items: ImportCandidate[];
      duplicates_not_written: number;
      conflicts: number;
      note: string;
    }>('/api/import/chatgpt/approve', { method: 'POST', body: JSON.stringify(payload) }),
  deleteImported: () =>
    request<{ deleted: number; note: string }>('/api/import/chatgpt', { method: 'DELETE' }),

  sites: () => request<{ sites: SiteAlias[] }>('/api/sites'),
  saveSite: (payload: { name: string; url: string; aliases?: string[] }) =>
    request<{ saved: boolean; was_new: boolean }>('/api/sites', {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  deleteSite: (name: string) =>
    request<{ deleted: boolean }>(`/api/sites/${encodeURIComponent(name)}`, { method: 'DELETE' }),

  reminders: (includeFinished = false) =>
    request<{ reminders: ReminderRecord[]; note: string }>(
      `/api/reminders?include_finished=${includeFinished}`,
    ),
  addReminder: (payload: { text: string; due_at?: string; minutes?: number; speak?: boolean }) =>
    request<{ reminder: ReminderRecord; note: string }>('/api/reminders', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  cancelReminder: (id: number) =>
    request<{ cancelled: boolean }>(`/api/reminders/${id}`, { method: 'DELETE' }),
  testReminder: (id: number) =>
    request<{ delivered: DueReminder; note: string }>(`/api/reminders/${id}/test`, { method: 'POST' }),
};

export type VoiceEngineInfoLike = {
  available: boolean;
  engine?: string;
  detail?: string;
  [key: string]: unknown;
};

export async function loadSnapshot(): Promise<SnapshotPayload> {
  const [health, tasks, messages, notifications, settings, phone] = await Promise.all([
    api.health(),
    api.tasks(),
    api.messages(),
    api.notifications(),
    api.settings(),
    api.phoneStatus(),
  ]);
  return {
    health,
    settings: settings.settings,
    tasks: tasks.tasks,
    messages: messages.messages,
    notifications: notifications.notifications,
    phone,
  };
}

export function socketUrl(): string {
  return `${WS_BASE}/ws`;
}

export type { JarvisEvent };
