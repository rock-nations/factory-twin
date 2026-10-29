import { Injectable, computed, inject, signal } from '@angular/core';

import { ChatMessage, ChatServerMessage, LiveStep } from '../models/chat';
import { ChatSocketService } from '../services/chat-socket.service';
import { UiStore } from './ui.store';

const SESSION_KEY = 'ft.chat.session';

function loadSessionId(): string {
  try {
    const saved = localStorage.getItem(SESSION_KEY);
    if (saved) return saved;
  } catch {
    // storage can be unavailable (private mode); fall through to a fresh id
  }
  const id = crypto.randomUUID();
  try {
    localStorage.setItem(SESSION_KEY, id);
  } catch {
    // ignore
  }
  return id;
}

@Injectable({ providedIn: 'root' })
export class ChatStore {
  private readonly socket = inject(ChatSocketService);
  private readonly ui = inject(UiStore);
  private started = false;
  private nextId = 1;

  readonly sessionId = loadSessionId();
  readonly messages = signal<ChatMessage[]>([]);
  readonly connection = this.socket.status;
  /** text to put into the composer ("Ask about this" from an alarm toast) */
  readonly draft = signal('');
  readonly unread = signal(0);
  readonly busy = computed(() => this.messages().some((m) => m.streaming));

  init(): void {
    if (this.started) return;
    this.started = true;
    this.socket.messages$.subscribe((m) => this.handle(m));
    this.socket.connect();
  }

  send(text: string): boolean {
    const message = text.trim();
    if (!message || this.busy()) return false;
    this.append({ role: 'user', text: message });
    if (!this.socket.sendUserMessage(this.sessionId, message)) {
      this.append({ role: 'error', text: 'Not connected to the chat service. Reconnecting…' });
      return false;
    }
    this.append({ role: 'assistant', text: '', streaming: true, steps: [] });
    return true;
  }

  /** Opens the drawer with a prepared question the user can edit and send. */
  prefill(text: string): void {
    this.draft.set(text);
    this.ui.openChat();
    this.unread.set(0);
  }

  clear(): void {
    this.messages.set([]);
  }

  handle(msg: ChatServerMessage): void {
    switch (msg.type) {
      case 'route':
        this.patchStreaming((m) => ({ ...m, route: msg.route }));
        break;
      case 'tool_call':
        this.patchStreaming((m) => ({
          ...m,
          steps: [...(m.steps ?? []), { name: msg.name, args: msg.args, done: false }],
        }));
        break;
      case 'tool_result':
        this.patchStreaming((m) => ({ ...m, steps: completeStep(m.steps ?? [], msg.name, msg.summary) }));
        break;
      case 'token':
        this.patchStreaming((m) => ({ ...m, text: m.text + msg.delta }));
        break;
      case 'done':
        this.patchStreaming((m) => ({
          ...m,
          text: msg.answer,
          streaming: false,
          trace: msg.trace,
          route: msg.trace.route,
        }));
        break;
      case 'error':
        this.finishStreamingWithError(msg.message);
        break;
      case 'proactive':
        this.append({ role: 'proactive', text: msg.message, actions: msg.actions });
        if (!this.ui.chatOpen()) this.unread.update((n) => n + 1);
        break;
    }
  }

  private append(partial: Omit<ChatMessage, 'id'>): void {
    this.messages.update((list) => [...list, { id: this.nextId++, ...partial }]);
  }

  private patchStreaming(fn: (m: ChatMessage) => ChatMessage): void {
    this.messages.update((list) => {
      const idx = list.findLastIndex((m) => m.streaming);
      return idx < 0 ? list : list.map((m, i) => (i === idx ? fn(m) : m));
    });
  }

  private finishStreamingWithError(message: string): void {
    this.messages.update((list) => {
      const withoutPlaceholder = list.filter((m) => !(m.streaming && m.text === ''));
      const stillStreaming = withoutPlaceholder.map((m) => (m.streaming ? { ...m, streaming: false } : m));
      return [...stillStreaming, { id: this.nextId++, role: 'error' as const, text: message }];
    });
  }
}

function completeStep(steps: LiveStep[], name: string, summary: string): LiveStep[] {
  const idx = steps.findIndex((s) => s.name === name && !s.done);
  return idx < 0 ? steps : steps.map((s, i) => (i === idx ? { ...s, summary, done: true } : s));
}
