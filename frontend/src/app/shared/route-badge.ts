import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { Route } from '../core/models/chat';

const LABELS: Record<Route, string> = {
  cypher: 'Text2Cypher',
  rag: 'Vector RAG',
  hybrid: 'Hybrid GraphRAG',
  direct: 'Direct',
};

@Component({
  selector: 'app-route-badge',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span class="badge" [class]="route()" data-testid="route-badge">{{ label() }}</span>`,
  styles: `
    .badge {
      display: inline-block;
      padding: 1px 8px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
      color: #0e1116;
    }
    .cypher { background: #58a6ff; }
    .rag { background: #d2a8ff; }
    .hybrid { background: #7ee787; }
    .direct { background: #8b97a7; }
  `,
})
export class RouteBadge {
  readonly route = input.required<Route>();
  protected label(): string {
    return LABELS[this.route()];
  }
}
