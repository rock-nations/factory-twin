import { SocketLike } from './core/services/reconnecting-socket';

/** Controllable stand-in for a WebSocket. */
export class FakeSocket implements SocketLike {
  static instances: FakeSocket[] = [];
  onopen: ((ev: Event) => unknown) | null = null;
  onclose: ((ev: CloseEvent) => unknown) | null = null;
  onerror: ((ev: Event) => unknown) | null = null;
  onmessage: ((ev: MessageEvent) => unknown) | null = null;
  readyState = 0;
  sent: string[] = [];
  closed = false;

  constructor(readonly url: string) {
    FakeSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.closed = true;
    this.readyState = 3;
    this.onclose?.(new Event('close') as CloseEvent);
  }

  // test helpers
  open(): void {
    this.readyState = 1;
    this.onopen?.(new Event('open'));
  }

  receive(payload: unknown): void {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent);
  }

  drop(): void {
    this.readyState = 3;
    this.onclose?.(new Event('close') as CloseEvent);
  }

  static reset(): void {
    FakeSocket.instances = [];
  }

  static get last(): FakeSocket {
    return FakeSocket.instances[FakeSocket.instances.length - 1];
  }
}

export const fakeSocketFactory = (url: string): SocketLike => new FakeSocket(url);
