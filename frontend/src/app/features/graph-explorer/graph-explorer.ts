import { KeyValuePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, effect, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { Router, RouterLink } from '@angular/router';

import { GraphView } from '../../core/models/factory';
import { ApiService } from '../../core/services/api.service';
import { GraphViewComponent, LABEL_COLORS } from '../../shared/graph-view';
import { merge } from '../machine-detail/machine-detail';

const DEFAULT_NODE = 'M017';

@Component({
  selector: 'app-graph-explorer',
  imports: [FormsModule, MatButtonModule, GraphViewComponent, KeyValuePipe, RouterLink],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './graph-explorer.html',
  styleUrl: './graph-explorer.scss',
})
export class GraphExplorer {
  /** `?node=` query param (component input binding) */
  readonly node = input<string | undefined>();

  private readonly api = inject(ApiService);
  private readonly router = inject(Router);

  protected readonly query = signal(DEFAULT_NODE);
  protected readonly depth = signal(1);
  protected readonly graph = signal<GraphView | null>(null);
  protected readonly selectedId = signal<string | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly legend = Object.entries(LABEL_COLORS);

  protected readonly selected = computed(() => this.graph()?.nodes.find((n) => n.id === this.selectedId()) ?? null);

  constructor() {
    effect(() => {
      const start = this.node() ?? DEFAULT_NODE;
      this.query.set(start);
      this.load(start);
    });
  }

  protected submit(): void {
    const id = this.query().trim();
    if (!id) return;
    void this.router.navigate([], { queryParams: { node: id } });
  }

  private load(id: string): void {
    this.error.set(null);
    this.api.getNeighborhood(id, this.depth()).subscribe({
      next: (g) => {
        this.graph.set(g);
        this.selectedId.set(id);
      },
      error: () => this.error.set(`No node with id "${id}".`),
    });
  }

  protected select(id: string): void {
    this.selectedId.set(id);
  }

  protected expand(id: string): void {
    this.api.getNeighborhood(id, 1).subscribe((g) => {
      this.graph.set(merge(this.graph(), g));
      this.selectedId.set(id);
    });
  }

  protected setDepth(value: number): void {
    this.depth.set(value);
    this.load(this.node() ?? DEFAULT_NODE);
  }
}
