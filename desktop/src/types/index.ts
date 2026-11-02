export type Risk = 'low' | 'moderate' | 'high';

export type TaskStatus =
  | 'pending'
  | 'running'
  | 'awaiting_confirmation'
  | 'completed'
  | 'failed'
  | 'cancelled';

export interface Task {
  id: string;
  text: string;
  source: string;
  intent: string | null;
  tool: string | null;
  args: Record<string, unknown>;
  risk: Risk | null;
  status: TaskStatus;
  result: Record<string, unknown> | null;
  error: string | null;
  planner: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  cancel_requested: boolean;
}

export interface TaskEvent {
  id: number;
  task_id: string;
  at: string;
  level: string;
  message: string;
}

export interface Message {
  id: number;
  at: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  task_id: string | null;
}

export interface ProviderStatus {
  name: string;
  kind: 'local' | 'cloud' | string;
  available: boolean;
  detail: string;
  model: string | null;
}

export interface VoiceEngineInfo {
  available: boolean;
  engine?: string;
  detail?: string;
  [key: string]: unknown;
}

export interface PhoneStatus {
  connected: boolean;
  device_id: string | null;
  name: string | null;
  battery: number | null;
  charging: boolean | null;
  last_seen: number | null;
  desktop_id: string;
  lan_ip: string;
  port: number;
  bound_host: string;
}

export interface Health {
  status: 'ok' | 'degraded';
  version: string;
  uptime_seconds: number;
  database: { ok: boolean; schema: number };
  providers: ProviderStatus[];
  active_provider: string | null;
  tools: number;
  voice: { backend_stt: VoiceEngineInfo; backend_tts: VoiceEngineInfo };
  phone: PhoneStatus;
  bound_host: string;
  permissions: { read: string[]; write: string[] };
}

export interface BatteryStats {
  available: boolean;
  percent?: number;
  plugged_in?: boolean;
  charging?: boolean;
  time_left_minutes?: number | null;
  reason?: string;
}

export interface SystemStats {
  timestamp: string;
  cpu: {
    percent: number;
    cores: number | null;
    physical_cores: number | null;
    frequency_mhz: number | null;
  };
  memory: {
    total_gb: number;
    used_gb: number;
    available_gb: number;
    percent: number;
  };
  battery: BatteryStats;
  disk: { path: string; total_gb: number; used_gb: number; free_gb: number; percent: number };
  network: {
    internet: boolean;
    connected: boolean;
    interfaces: { name: string; up: boolean; speed_mbps: number }[];
    active_interfaces: string[];
  };
  uptime: { seconds: number; humanized: string };
  os: { system: string; release: string; version: string; machine: string; hostname: string; python: string };
  process_count: number;
  processes?: ProcessInfo[];
  error?: string;
}

export interface ProcessInfo {
  pid: number;
  name: string;
  memory_mb: number | null;
  cpu_percent: number | null;
}

export interface WindowInfo {
  hwnd: number;
  title: string;
  pid: number;
  process: string | null;
}

export interface RunningProcess {
  pid: number;
  name: string | null;
  user: string | null;
}

export interface AppEntry {
  name: string;
  aliases: string[];
  kind: string;
  target: string;
  available: boolean;
  builtin: boolean;
  favorite: boolean;
}

export interface Notification {
  id: number;
  at: string;
  source: string;
  title: string;
  body: string;
  read: number;
}

export interface Confirmation {
  task_id: string;
  token: string;
  summary: string;
  risk: Risk;
}

export interface CommandResponse {
  type: 'task' | 'reply';
  task?: Task | null;
  reply?: string;
  requires_confirmation?: boolean;
  confirmation?: Confirmation | null;
  duplicate?: boolean;
  message?: Message;
  intent?: string | null;
}

export interface DeviceRecord {
  id: string;
  name: string;
  paired_at: string;
  last_seen: string | null;
  revoked: number;
}

export interface PairingInfo {
  code: string;
  expires_in: number;
  ws_url_loopback: string;
  ws_url_lan: string;
  desktop_id: string;
  desktop_name: string;
  note: string;
}

export interface IntegrationRecord {
  provider: string;
  config: Record<string, unknown>;
  enabled: boolean;
  updated_at?: string;
}

export interface AuditEntry {
  id: number;
  at: string;
  actor: string;
  action: string;
  risk: string;
  detail: Record<string, unknown>;
  outcome: string;
}

export type SettingsMap = Record<string, unknown>;

export type ReactorState =
  | 'idle'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'executing'
  | 'completed'
  | 'interrupted'
  | 'error'
  | 'offline'
  | 'permission';

export type TabId =
  | 'dashboard'
  | 'command'
  | 'chat'
  | 'monitor'
  | 'phone'
  | 'automation'
  | 'memory'
  | 'settings';

export interface ReminderRecord {
  id: number;
  text: string;
  due_at: string;
  speak: boolean;
  fired: boolean;
  cancelled: boolean;
  fired_at: string | null;
  source: string | null;
  created_at: string;
}

export interface KnowledgeItem {
  key: string;
  value: unknown;
  kind: string | null;
  category: string;
  source: string;
  verified: boolean;
  as_of: string | null;
  note: string | null;
  updated_at: string;
}

export interface ImportConversation {
  id: string;
  name: string;
  messages: number;
  user_messages: number;
  started: string | null;
  updated: string | null;
  excluded: boolean;
}

export interface ImportCandidate {
  id: string;
  text: string;
  category: string;
  conversation: string;
  conversation_id: string;
  said_at: string | null;
  duplicate_of: string | null;
  conflicts_with: string | null;
  reason: string;
}

export interface SiteAlias {
  name: string;
  url: string;
  aliases: string[];
  builtin: boolean;
}

export interface DueReminder {
  id: number;
  text: string;
  due_at: string;
  speak: boolean;
  late: boolean;
  late_minutes: number;
  test?: boolean;
}

export type ThemeName = 'blue' | 'amber';

export interface HardwareInfo {
  cpu_name: string | null;
  gpus: string[];
  device_model: string | null;
  system: string;
  release: string;
}

export interface MemoryRecord {
  key: string;
  value: unknown;
  kind: string;
  updated_at: string;
}

export interface JarvisEvent {
  type: string;
  payload: Record<string, unknown>;
  at?: string;
}

export interface SnapshotPayload {
  health: Health;
  settings: SettingsMap;
  tasks: Task[];
  messages: Message[];
  notifications: Notification[];
  phone: PhoneStatus;
}
