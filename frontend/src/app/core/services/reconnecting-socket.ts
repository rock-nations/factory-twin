import { InjectionToken, signal } from '@angular/core';
import { Observable, Subject } from 'rxjs';

export type SocketStatus = 'connecting' | 'open' | 'closed';

export interface SocketLike {
  onopen: ((ev: Event) => unknown) | null;
  onclose: ((ev: CloseEvent) => unknown) | null;
  onerror: ((ev: Event) => unknown) | null;
  onmessage: ((ev: MessageEvent) => unknown) | null;
  send(data: string): void;
  close(): void;
  readyState: number;
}

/** Overridable in tests so no real network connection is opened. */
export const SOCKET_FACTORY = new InjectionToken<(url: string) => SocketLike>('SOCKET_FACTORY', {
  providedIn: 'root',
  factory: () => (url) => new WebSocket(url),
});

export const BASE_DELAY_MS = 500;
export const MAX_DELAY_MS = 15_000;

export function backoffDelay(attempt: number, random: () => number = Math.random): number {
  const exp = Math.min(MAX_DELAY_MS, BASE_DELAY_MS * 2 ** attempt);
  return Math.round(exp * (0.75 + random() * 0.25)); // jitter so clients do not reconnect in lockstep
}

export function socketUrl(path: string): string {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  return `${scheme}://${location.host}${path}`;
}

/** JSON WebSocket that reconnects with exponential backoff and exposes its status as a signal. */
export class ReconnectingSocket<T> {
  readonly status = signal<SocketStatus>('closed');
  private readonly subject = new Subject<T>();
  readonly messages$: Observable<T> = this.subject.asObservable();

  private socket: SocketLike | null = null;
  private attempt = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private wanted = false;

  constructor(
    private readonly path: string,
    private readonly factory: (url: string) => SocketLike = (url) => new WebSocket(url),
  ) {}

  connect(): void {
    this.wanted = true;
    if (this.socket || this.timer) return;
    this.open();
  }

  send(payload: unknown): boolean {
    if (!this.socket || this.status() !== 'open') return false;
    this.socket.send(JSON.stringify(payload));
    return true;
  }

  close(): void {
    this.wanted = false;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.socket?.close();
    this.socket = null;
    this.status.set('closed');
  }

  private open(): void {
    this.status.set('connecting');
    const socket = this.factory(socketUrl(this.path));
    this.socket = socket;
    socket.onopen = () => {
      this.attempt = 0;
      this.status.set('open');
    };
    socket.onmessage = (ev) => {
      try {
        this.subject.next(JSON.parse(String(ev.data)) as T);
      } catch {
        // ignore frames that are not JSON
      }
    };
    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.socket = null;
      this.status.set('closed');
      if (this.wanted) this.scheduleReconnect();
    };
    socket.onerror = () => socket.close();
  }

  private scheduleReconnect(): void {
    const delay = backoffDelay(this.attempt++);
    this.timer = setTimeout(() => {
      this.timer = null;
      if (this.wanted) this.open();
    }, delay);
  }
}
