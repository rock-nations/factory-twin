import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { FakeSocket } from '../../testing';
import { BASE_DELAY_MS, MAX_DELAY_MS, ReconnectingSocket, backoffDelay } from './reconnecting-socket';

describe('backoffDelay', () => {
  it('grows exponentially and is capped', () => {
    const noJitter = () => 1;
    expect(backoffDelay(0, noJitter)).toBe(BASE_DELAY_MS);
    expect(backoffDelay(1, noJitter)).toBe(BASE_DELAY_MS * 2);
    expect(backoffDelay(3, noJitter)).toBe(BASE_DELAY_MS * 8);
    expect(backoffDelay(50, noJitter)).toBe(MAX_DELAY_MS);
  });

  it('applies up to 25% negative jitter', () => {
    expect(backoffDelay(2, () => 0)).toBe(Math.round(BASE_DELAY_MS * 4 * 0.75));
  });
});

describe('ReconnectingSocket', () => {
  beforeEach(() => {
    FakeSocket.reset();
    vi.useFakeTimers();
  });
  afterEach(() => vi.useRealTimers());

  const make = () => new ReconnectingSocket<{ n: number }>('/ws/test', (url) => new FakeSocket(url));

  it('connects to the same host with the ws scheme and tracks status', () => {
    const s = make();
    expect(s.status()).toBe('closed');
    s.connect();
    expect(s.status()).toBe('connecting');
    expect(FakeSocket.last.url).toMatch(/^wss?:\/\/.+\/ws\/test$/);
    FakeSocket.last.open();
    expect(s.status()).toBe('open');
  });

  it('emits parsed messages and ignores non-JSON frames', () => {
    const s = make();
    const got: { n: number }[] = [];
    s.messages$.subscribe((m) => got.push(m));
    s.connect();
    FakeSocket.last.open();
    FakeSocket.last.receive({ n: 1 });
    FakeSocket.last.onmessage?.({ data: 'not json' } as MessageEvent);
    FakeSocket.last.receive({ n: 2 });
    expect(got).toEqual([{ n: 1 }, { n: 2 }]);
  });

  it('only sends while open', () => {
    const s = make();
    s.connect();
    expect(s.send({ a: 1 })).toBe(false);
    FakeSocket.last.open();
    expect(s.send({ a: 1 })).toBe(true);
    expect(FakeSocket.last.sent).toEqual(['{"a":1}']);
  });

  it('reconnects with growing delays and resets the delay after a successful open', () => {
    const s = make();
    s.connect();
    FakeSocket.last.drop(); // attempt 0 -> ~500ms
    expect(s.status()).toBe('closed');
    expect(FakeSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(BASE_DELAY_MS);
    expect(FakeSocket.instances).toHaveLength(2);

    FakeSocket.last.drop(); // attempt 1 -> ~1000ms, not yet at 500ms
    vi.advanceTimersByTime(BASE_DELAY_MS * 0.7);
    expect(FakeSocket.instances).toHaveLength(2);
    vi.advanceTimersByTime(BASE_DELAY_MS * 2);
    expect(FakeSocket.instances).toHaveLength(3);

    FakeSocket.last.open(); // success resets the backoff
    FakeSocket.last.drop();
    vi.advanceTimersByTime(BASE_DELAY_MS);
    expect(FakeSocket.instances).toHaveLength(4);
  });

  it('does not reconnect after close()', () => {
    const s = make();
    s.connect();
    FakeSocket.last.open();
    s.close();
    vi.advanceTimersByTime(60_000);
    expect(FakeSocket.instances).toHaveLength(1);
    expect(s.status()).toBe('closed');
  });

  it('connect() is idempotent', () => {
    const s = make();
    s.connect();
    s.connect();
    expect(FakeSocket.instances).toHaveLength(1);
  });
});
