import type { JarvisEvent, SnapshotPayload } from '../types';
import { socketUrl } from './api';

export type SocketStatus = 'connecting' | 'online' | 'offline';

type SnapshotHandler = (payload: SnapshotPayload) => void;
type EventHandler = (event: JarvisEvent) => void;
type StatusHandler = (status: SocketStatus) => void;

/** Reconnecting WebSocket client for the dashboard event channel. */
export class JarvisSocket {
  private socket: WebSocket | null = null;
  private timer: number | null = null;
  private attempts = 0;
  private stopped = false;

  constructor(
    private readonly onSnapshot: SnapshotHandler,
    private readonly onEvent: EventHandler,
    private readonly onStatus: StatusHandler,
  ) {}

  connect(): void {
    this.stopped = false;
    this.open();
  }

  close(): void {
    this.stopped = true;
    if (this.timer !== null) {
      window.clearTimeout(this.timer);
      this.timer = null;
    }
    this.socket?.close();
    this.socket = null;
  }

  private open(): void {
    if (this.stopped) return;
    this.onStatus(this.attempts === 0 ? 'connecting' : 'offline');
    let socket: WebSocket;
    try {
      socket = new WebSocket(socketUrl());
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;

    socket.onopen = () => {
      this.attempts = 0;
      this.onStatus('online');
    };

    socket.onmessage = (message) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(String(message.data));
      } catch {
        return;
      }
      if (!parsed || typeof parsed !== 'object') return;
      const event = parsed as JarvisEvent;
      if (event.type === 'snapshot') {
        this.onSnapshot(event.payload as unknown as SnapshotPayload);
        this.onStatus('online');
        return;
      }
      if (event.type === 'ping') return;
      this.onEvent(event);
    };

    socket.onclose = () => {
      this.socket = null;
      if (this.stopped) return;
      this.onStatus('offline');
      this.scheduleReconnect();
    };

    socket.onerror = () => {
      socket.close();
    };
  }

  private scheduleReconnect(): void {
    if (this.stopped || this.timer !== null) return;
    const delay = Math.min(1000 * 2 ** this.attempts, 15000);
    this.attempts += 1;
    this.timer = window.setTimeout(() => {
      this.timer = null;
      this.open();
    }, delay);
  }
}
