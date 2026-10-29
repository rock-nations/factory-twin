import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { Trace } from '../models/chat';
import { SOCKET_FACTORY } from '../services/reconnecting-socket';
import { FakeSocket, fakeSocketFactory } from '../../testing';
import { ChatStore } from './chat.store';
import { UiStore } from './ui.store';

const trace: Trace = {
  route: 'cypher', tools: [], llm_calls: [], rounds: 1, total_latency_ms: 10, input_tokens: 5,
  output_tokens: 5, cost_usd: 0.001, step_latency_ms: {},
};

describe('ChatStore', () => {
  let chat: ChatStore;
  let ui: UiStore;

  beforeEach(() => {
    FakeSocket.reset();
    TestBed.configureTestingModule({ providers: [{ provide: SOCKET_FACTORY, useValue: fakeSocketFactory }] });
    chat = TestBed.inject(ChatStore);
    ui = TestBed.inject(UiStore);
    chat.init();
  });

  const open = () => FakeSocket.last.open();
  const server = (m: unknown) => FakeSocket.last.receive(m);

  it('sends a user_message with the session id and shows a streaming placeholder', () => {
    open();
    expect(chat.send('hello')).toBe(true);
    expect(JSON.parse(FakeSocket.last.sent[0])).toEqual({ type: 'user_message', session_id: chat.sessionId, message: 'hello' });
    const [user, bot] = chat.messages();
    expect(user).toMatchObject({ role: 'user', text: 'hello' });
    expect(bot).toMatchObject({ role: 'assistant', streaming: true, text: '' });
    expect(chat.busy()).toBe(true);
  });

  it('refuses to send while a reply is streaming or the text is blank', () => {
    open();
    chat.send('one');
    expect(chat.send('two')).toBe(false);
    expect(chat.send('   ')).toBe(false);
    expect(chat.messages()).toHaveLength(2);
  });

  it('shows an error instead of losing the message when disconnected', () => {
    expect(chat.send('hello')).toBe(false);
    expect(chat.messages().map((m) => m.role)).toEqual(['user', 'error']);
    expect(chat.busy()).toBe(false);
  });

  it('follows the streaming protocol: route, tool steps, tokens, done', () => {
    open();
    chat.send('which machines?');
    server({ type: 'route', route: 'cypher' });
    server({ type: 'tool_call', name: 'query_twin_data', args: { question: 'q' } });
    let bot = chat.messages()[1];
    expect(bot.route).toBe('cypher');
    expect(bot.steps).toEqual([{ name: 'query_twin_data', args: { question: 'q' }, done: false }]);

    server({ type: 'tool_result', name: 'query_twin_data', summary: '3 row(s)', sources: [], cypher: 'MATCH', row_count: 3 });
    expect(chat.messages()[1].steps![0]).toMatchObject({ done: true, summary: '3 row(s)' });

    server({ type: 'token', delta: 'M017 ' });
    server({ type: 'token', delta: 'is down.' });
    expect(chat.messages()[1].text).toBe('M017 is down.');
    expect(chat.busy()).toBe(true);

    server({ type: 'done', answer: 'M017 is down. [1]', trace });
    bot = chat.messages()[1];
    expect(bot).toMatchObject({ text: 'M017 is down. [1]', streaming: false, route: 'cypher' });
    expect(bot.trace).toEqual(trace);
    expect(chat.busy()).toBe(false);
  });

  it('updates the route when a later round changes it', () => {
    open();
    chat.send('x');
    server({ type: 'route', route: 'cypher' });
    server({ type: 'route', route: 'hybrid' });
    expect(chat.messages()[1].route).toBe('hybrid');
  });

  it('turns a server error into an error message and stops streaming', () => {
    open();
    chat.send('x');
    server({ type: 'error', message: 'OPENAI_API_KEY is not set' });
    expect(chat.messages().map((m) => m.role)).toEqual(['user', 'error']);
    expect(chat.messages()[1].text).toContain('OPENAI_API_KEY');
    expect(chat.busy()).toBe(false);
  });

  it('keeps the partial answer when an error arrives mid-stream', () => {
    open();
    chat.send('x');
    server({ type: 'token', delta: 'partial' });
    server({ type: 'error', message: 'boom' });
    expect(chat.messages().map((m) => [m.role, m.streaming ?? false])).toEqual([['user', false], ['assistant', false], ['error', false]]);
    expect(chat.messages()[1].text).toBe('partial');
  });

  it('adds proactive messages with their actions and counts them as unread while closed', () => {
    open();
    server({ type: 'proactive', message: '⚠️ M017 vibration 3.1σ above normal.', actions: [{ label: 'Diagnose M017', message: 'Diagnose M017' }], machine_id: 'M017' });
    expect(chat.messages()[0]).toMatchObject({ role: 'proactive', actions: [{ label: 'Diagnose M017', message: 'Diagnose M017' }] });
    expect(chat.unread()).toBe(1);
    ui.openChat();
    server({ type: 'proactive', message: 'again', actions: [] });
    expect(chat.unread()).toBe(1);
  });

  it('prefill opens the drawer and sets the draft', () => {
    chat.unread.set(2);
    chat.prefill('Diagnose M017: vibration alarm');
    expect(ui.chatOpen()).toBe(true);
    expect(chat.draft()).toBe('Diagnose M017: vibration alarm');
    expect(chat.unread()).toBe(0);
  });

  it('a proactive message does not interrupt a streaming answer', () => {
    open();
    chat.send('x');
    server({ type: 'proactive', message: 'alert', actions: [] });
    server({ type: 'token', delta: 'answer' });
    const roles = chat.messages().map((m) => m.role);
    expect(roles).toEqual(['user', 'assistant', 'proactive']);
    expect(chat.messages()[1].text).toBe('answer');
  });

  it('clear() empties the conversation', () => {
    open();
    chat.send('x');
    chat.clear();
    expect(chat.messages()).toEqual([]);
  });
});
