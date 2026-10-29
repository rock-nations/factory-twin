import { ChangeDetectionStrategy, Component, ElementRef, computed, effect, inject, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatIconModule } from '@angular/material/icon';

import { ChatStore } from '../../core/state/chat.store';
import { CHAT_DEFAULT_WIDTH, UiStore } from '../../core/state/ui.store';
import { StatusDot } from '../../shared/status-dot';
import { ChatMessageView } from './chat-message';
import { SampleQuestions } from './sample-questions';

@Component({
  selector: 'app-chat-drawer',
  imports: [FormsModule, MatIconModule, StatusDot, ChatMessageView, SampleQuestions],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './chat-drawer.html',
  styleUrl: './chat-drawer.scss',
})
export class ChatDrawer {
  protected readonly chat = inject(ChatStore);
  protected readonly ui = inject(UiStore);
  protected readonly wide = computed(() => this.ui.chatWidth() > CHAT_DEFAULT_WIDTH + 40);
  /** the sample-question browser; always shown while the conversation is empty */
  protected readonly samplesOpen = signal(false);
  protected readonly text = signal('');
  protected readonly showSamples = computed(() => this.samplesOpen() || this.chat.messages().length === 0);

  private readonly list = viewChild<ElementRef<HTMLElement>>('list');
  private readonly input = viewChild<ElementRef<HTMLTextAreaElement>>('input');

  constructor() {
    // an alarm toast's "Ask about this" puts a prepared question into the composer
    effect(() => {
      const draft = this.chat.draft();
      if (!draft) return;
      this.text.set(draft);
      this.chat.draft.set('');
      queueMicrotask(() => this.input()?.nativeElement.focus());
    });
    effect(() => {
      this.chat.messages();
      queueMicrotask(() => {
        const el = this.list()?.nativeElement;
        if (el) el.scrollTop = el.scrollHeight;
      });
    });
  }

  protected tone() {
    const s = this.chat.connection();
    return s === 'open' ? 'ok' : s === 'connecting' ? 'warn' : 'error';
  }

  protected send(text = this.text()): void {
    if (this.chat.send(text)) {
      this.text.set('');
      this.samplesOpen.set(false);
    }
  }

  protected editSample(question: string): void {
    this.text.set(question);
    this.samplesOpen.set(false);
    queueMicrotask(() => this.input()?.nativeElement.focus());
  }

  protected onKey(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.send();
    }
  }

  protected startResize(event: PointerEvent): void {
    const handle = event.currentTarget as HTMLElement;
    handle.setPointerCapture(event.pointerId);
    event.preventDefault();
  }

  protected resize(event: PointerEvent): void {
    const handle = event.currentTarget as HTMLElement;
    if (!handle.hasPointerCapture(event.pointerId)) return;
    // the panel is docked to the right edge, so its width is the distance from the pointer to it
    this.ui.setChatWidth(window.innerWidth - event.clientX);
  }

  protected onResizeKey(event: KeyboardEvent): void {
    const step = event.shiftKey ? 80 : 20;
    if (event.key === 'ArrowLeft') this.ui.setChatWidth(this.ui.chatWidth() + step);
    else if (event.key === 'ArrowRight') this.ui.setChatWidth(this.ui.chatWidth() - step);
    else return;
    event.preventDefault();
  }
}
