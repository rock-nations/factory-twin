import { DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, input, signal } from '@angular/core';

import { ToolTrace, Trace } from '../../core/models/chat';
import { RouteBadge } from '../../shared/route-badge';

export function previewColumns(rows: Record<string, unknown>[]): string[] {
  const cols: string[] = [];
  for (const row of rows) for (const key of Object.keys(row)) if (!cols.includes(key)) cols.push(key);
  return cols;
}

export function cell(value: unknown): string {
  if (value === null || value === undefined) return '';
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}

@Component({
  selector: 'app-trace-panel',
  imports: [DecimalPipe, RouteBadge],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './trace-panel.html',
  styleUrl: './trace-panel.scss',
})
export class TracePanel {
  readonly trace = input.required<Trace>();
  protected readonly copied = signal<string | null>(null);

  protected readonly columns = previewColumns;
  protected readonly cell = cell;

  protected args(tool: ToolTrace): string {
    return JSON.stringify(tool.arguments);
  }

  protected async copy(text: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      this.copied.set(text);
      setTimeout(() => this.copied.set(null), 1500);
    } catch {
      // clipboard may be blocked (insecure context); the text is still selectable
    }
  }
}
