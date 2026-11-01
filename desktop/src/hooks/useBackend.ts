import { useCallback, useEffect, useRef, useState } from 'react';

import { api, ApiError, loadSnapshot } from '../services/api';
import { JarvisSocket, type SocketStatus } from '../services/ws';
import type {
  Confirmation,
  DueReminder,
  Health,
  JarvisEvent,
  Message,
  Notification,
  PhoneStatus,
  SettingsMap,
  SnapshotPayload,
  Task,
} from '../types';

export interface PendingConfirmation extends Confirmation {
  task?: Task;
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return String(error);
}

function sortTasks(tasks: Task[]): Task[] {
  return [...tasks].sort((a, b) => b.created_at.localeCompare(a.created_at));
}

function upsertTask(tasks: Task[], task: Task): Task[] {
  const index = tasks.findIndex((item) => item.id === task.id);
  if (index === -1) return sortTasks([task, ...tasks]);
  const next = tasks.slice();
  next[index] = task;
  return sortTasks(next);
}

function appendMessage(messages: Message[], message: Message): Message[] {
  if (messages.some((item) => item.id === message.id)) return messages;
  return [...messages, message].slice(-200);
}

export function useBackend() {
  const [connection, setConnection] = useState<SocketStatus>('connecting');
  const [health, setHealth] = useState<Health | null>(null);
  const [settings, setSettings] = useState<SettingsMap>({});
  const [tasks, setTasks] = useState<Task[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [phone, setPhone] = useState<PhoneStatus | null>(null);
  const [pending, setPending] = useState<PendingConfirmation | null>(null);
  const [dueReminder, setDueReminder] = useState<DueReminder | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const socketRef = useRef<JarvisSocket | null>(null);
  const healthRef = useRef<Health | null>(null);

  useEffect(() => {
    healthRef.current = health;
  }, [health]);

  const applyPhone = useCallback((next: PhoneStatus) => {
    setPhone(next);
    setHealth((current) => (current ? { ...current, phone: next } : current));
  }, []);

  const applySnapshot = useCallback((snapshot: SnapshotPayload) => {
    setHealth(snapshot.health);
    setSettings(snapshot.settings);
    setTasks(sortTasks(snapshot.tasks));
    setMessages(snapshot.messages);
    setNotifications(snapshot.notifications);
    setPhone(snapshot.phone);
  }, []);

  const refreshNotifications = useCallback(() => {
    void api
      .notifications()
      .then((data) => setNotifications(data.notifications))
      .catch(() => undefined);
  }, []);

  const refreshPhone = useCallback(() => {
    void api
      .phoneStatus()
      .then((data) => applyPhone(data))
      .catch(() => undefined);
  }, [applyPhone]);

  const handleEvent = useCallback(
    (event: JarvisEvent) => {
      const payload = event.payload;
      switch (event.type) {
        case 'task.created':
        case 'task.updated':
          setTasks((current) => upsertTask(current, payload as unknown as Task));
          break;
        case 'confirmation.required': {
          const task = payload.task as Task | undefined;
          if (task) {
            setTasks((current) => upsertTask(current, task));
            setPending({
              task_id: task.id,
              token: String(task.confirmation_token ?? ''),
              summary: String(payload.summary ?? ''),
              risk: (task.risk ?? 'moderate') as Confirmation['risk'],
              task,
            });
          }
          break;
        }
        case 'message.created':
          setMessages((current) => appendMessage(current, payload as unknown as Message));
          break;
        case 'messages.cleared':
          setMessages([]);
          break;
        case 'phone.status':
          applyPhone(payload as unknown as PhoneStatus);
          break;
        case 'phone.connected':
        case 'phone.disconnected':
          refreshPhone();
          break;
        case 'notification.created':
          refreshNotifications();
          break;
        case 'reminder.due':
          // The scheduler runs inside the backend; this is how the desktop hears it.
          setDueReminder(payload as unknown as DueReminder);
          refreshNotifications();
          break;
        case 'settings.updated': {
          const key = String(payload.key ?? '');
          if (key) setSettings((current) => ({ ...current, [key]: payload.value }));
          break;
        }
        default:
          break;
      }
    },
    [applyPhone, refreshPhone, refreshNotifications],
  );

  const refresh = useCallback(async (silent = false) => {
    try {
      const snapshot = await loadSnapshot();
      applySnapshot(snapshot);
    } catch (cause) {
      if (!silent) setError(errorMessage(cause));
    }
  }, [applySnapshot]);

  useEffect(() => {
    void refresh(true);
    const socket = new JarvisSocket(applySnapshot, handleEvent, setConnection);
    socketRef.current = socket;
    socket.connect();
    return () => {
      socket.close();
      socketRef.current = null;
    };
  }, [applySnapshot, handleEvent, refresh]);

  const sendCommand = useCallback(
    async (
      payload: { text?: string; tool?: string; args?: Record<string, unknown> },
      source = 'text',
    ) => {
      setBusy(true);
      setError(null);
      try {
        const data = await api.command({ ...payload, source });
        if (data.task) setTasks((current) => upsertTask(current, data.task as Task));
        if (data.requires_confirmation && data.confirmation) {
          setPending({ ...data.confirmation, task: data.task ?? undefined });
        }
        if (data.message && connection !== 'online') {
          setMessages((current) => appendMessage(current, data.message as Message));
        }
        return data;
      } catch (cause) {
        setError(errorMessage(cause));
        return null;
      } finally {
        setBusy(false);
      }
    },
    [connection],
  );

  const confirmPending = useCallback(async () => {
    if (!pending) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.confirm(pending.task_id, pending.token);
      setTasks((current) => upsertTask(current, result.task));
      setPending(null);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }, [pending]);

  const dismissPending = useCallback(async () => {
    if (!pending) {
      setPending(null);
      return;
    }
    try {
      await api.cancel(pending.task_id);
    } catch {
      /* the task may already have moved on */
    }
    setPending(null);
  }, [pending]);

  const cancelTask = useCallback(async (id: string) => {
    try {
      const result = await api.cancel(id);
      setTasks((current) => upsertTask(current, result.task));
      setPending((current) => (current?.task_id === id ? null : current));
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }, []);

  const updateSettings = useCallback(async (values: SettingsMap) => {
    setError(null);
    try {
      const result = await api.patchSettings(values);
      setSettings(result.settings);
      return result;
    } catch (cause) {
      setError(errorMessage(cause));
      return null;
    }
  }, []);

  const clearConversation = useCallback(async () => {
    try {
      await api.clearMessages();
      setMessages([]);
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }, []);

  const markNotificationsRead = useCallback(async () => {
    try {
      await api.markNotificationsRead();
      setNotifications((current) => current.map((item) => ({ ...item, read: 1 })));
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }, []);

  const notifyPhone = useCallback(async (title: string, body: string) => {
    try {
      return await api.notifyPhone(title, body);
    } catch (cause) {
      setError(errorMessage(cause));
      return null;
    }
  }, []);

  const dismissError = useCallback(() => setError(null), []);

  const clearDueReminder = useCallback(() => setDueReminder(null), []);

  const unread = notifications.filter((item) => !item.read).length;

  return {
    connection,
    health,
    settings,
    tasks,
    messages,
    notifications,
    unread,
    phone,
    pending,
    dueReminder,
    error,
    busy,
    refresh,
    sendCommand,
    confirmPending,
    dismissPending,
    cancelTask,
    updateSettings,
    clearConversation,
    markNotificationsRead,
    notifyPhone,
    dismissError,
    clearDueReminder,
  };
}

export type Backend = ReturnType<typeof useBackend>;
