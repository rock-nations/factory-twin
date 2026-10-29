import { ChangeDetectionStrategy, Component, computed, inject, input, output } from '@angular/core';
import { Router } from '@angular/router';
import { marked } from 'marked';

import { ChatMessage } from '../../core/models/chat';
import { linkMachineIds } from '../../core/util/machine-ids';
import { RouteBadge } from '../../shared/route-badge';
import { TracePanel } from './trace-panel';

const TOOL_LABELS: Record<string, string> = {
  query_twin_data: 'Querying the graph',
  get_machine_context: 'Reading machine context',
  search_knowledge_base: 'Searching the manuals',
  find_similar_incidents: 'Finding similar incidents',
};

export function renderMarkdown(text: string): string {
  return linkMachineIds(marked.parse(text, { async: false, gfm: true }) as string);
}

@Component({
  selector: 'app-chat-message',
  imports: [TracePanel, RouteBadge],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <article class="msg" [class]="message().role" [attr.data-role]="message().role">
      @if (message().role === 'user') {
        <p class="plain">{{ message().text }}</p>
      } @else {
        @if (message().role === 'proactive') {
          <div class="tag">Alert</div>
        }
        @if (message().streaming && message().route) {
          <app-route-badge [route]="message().route!" />
        }
        @if (message().steps?.length) {
          <ul class="steps" data-testid="live-steps">
            @for (s of message().steps; track $index) {
              <li [class.done]="s.done">
                <span class="mark">{{ s.done ? '✓' : '…' }}</span>
                {{ label(s.name) }}@if (s.summary) { <span class="dim"> — {{ s.summary }}</span> }
              </li>
            }
          </ul>
        }
        @if (message().text) {
          <!-- marked output is sanitized by Angular's [innerHTML] -->
          <div class="md" data-testid="markdown" [innerHTML]="html()" (click)="onClick($event)"></div>
        } @else if (message().streaming) {
          <span class="thinking">Thinking…</span>
        }
        @if (message().actions?.length) {
          <div class="actions">
            @for (a of message().actions; track a.label) {
              <button type="button" (click)="action.emit(a.message)">{{ a.label }}</button>
            }
          </div>
        }
        @if (message().trace; as trace) {
          <app-trace-panel [trace]="trace" />
        }
      }
    </article>
  `,
  styleUrl: './chat-message.scss',
})
export class ChatMessageView {
  readonly message = input.required<ChatMessage>();
  readonly action = output<string>();

  private readonly router = inject(Router);
  protected readonly html = computed(() => renderMarkdown(this.message().text));

  protected label(name: string): string {
    return TOOL_LABELS[name] ?? name;
  }

  /** Machine ids in answers are plain links; route them in-app instead of reloading the page. */
  protected onClick(event: MouseEvent): void {
    const link = (event.target as HTMLElement).closest('a');
    const href = link?.getAttribute('href');
    if (href?.startsWith('/machines/')) {
      event.preventDefault();
      void this.router.navigateByUrl(href);
    }
  }
}
