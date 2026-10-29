import { DatePipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  effect,
  inject,
  input,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { RouterLink } from '@angular/router';

import { GraphView, MachineDetail as Detail, SENSOR_NAMES, SensorName, TelemetrySeries } from '../../core/models/factory';
import { ApiService } from '../../core/services/api.service';
import { ChatStore } from '../../core/state/chat.store';
import { FactoryStore } from '../../core/state/factory.store';
import { provideCharts } from '../../shared/echarts-setup';
import { GraphViewComponent } from '../../shared/graph-view';
import { ChartPoint, LineChart } from '../../shared/line-chart';
import { SensorGauge } from '../../shared/sensor-gauge';
import { StatusBadge } from '../../shared/status-badge';

const MAX_POINTS = 200;

@Component({
  selector: 'app-machine-detail',
  imports: [RouterLink, DatePipe, MatButtonModule, SensorGauge, StatusBadge, LineChart, GraphViewComponent],
  providers: [provideCharts()],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './machine-detail.html',
  styleUrl: './machine-detail.scss',
})
export class MachineDetail {
  /** Bound from the `:id` route param. */
  readonly id = input.required<string>();

  private readonly api = inject(ApiService);
  private readonly store = inject(FactoryStore);
  private readonly chat = inject(ChatStore);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly detail = signal<Detail | null>(null);
  protected readonly notFound = signal(false);
  protected readonly graph = signal<GraphView | null>(null);
  protected readonly series = signal<Record<SensorName, ChartPoint[]>>(emptySeries());
  protected readonly sensorNames = SENSOR_NAMES;

  /** live sensor values (and state) from the store, falling back to the loaded detail */
  protected readonly sensors = computed(() => this.store.byId().get(this.id())?.sensors ?? this.detail()?.sensors ?? []);
  protected readonly status = computed(() => this.store.byId().get(this.id())?.status ?? this.detail()?.machine.status ?? 'running');
  protected readonly alarms = computed(() =>
    this.store.alarms().filter((a) => a.machine_id === this.id() && a.status !== 'resolved'),
  );

  constructor() {
    effect(() => this.load(this.id()));
    this.store.sensorTicks$.pipe(takeUntilDestroyed(this.destroyRef)).subscribe((ticks) => {
      const mine = ticks.filter((t) => t.machine_id === this.id());
      if (!mine.length) return;
      this.series.update((s) => {
        const next = { ...s };
        for (const t of mine) next[t.sensor] = [...next[t.sensor], { ts: t.ts, value: t.value }].slice(-MAX_POINTS);
        return next;
      });
    });
  }

  private load(id: string): void {
    this.detail.set(null);
    this.notFound.set(false);
    this.series.set(emptySeries());
    this.api.getMachine(id).subscribe({
      next: (d) => this.detail.set(d),
      error: () => this.notFound.set(true),
    });
    this.api.getTelemetry(id).subscribe({
      next: (t) => this.series.set(toChartSeries(t)),
      error: () => undefined, // empty until the simulator has run
    });
    this.api.getNeighborhood(id, 1).subscribe({ next: (g) => this.graph.set(g), error: () => undefined });
  }

  protected sensor(name: SensorName) {
    return this.sensors().find((s) => s.sensor === name);
  }

  protected ack(alarmId: string): void {
    this.api.ackAlarm(alarmId).subscribe();
  }

  protected diagnose(): void {
    this.chat.prefill(`Diagnose ${this.id()}`);
  }

  protected expand(nodeId: string): void {
    this.api.getNeighborhood(nodeId, 1).subscribe((g) => this.graph.set(merge(this.graph(), g)));
  }
}

function emptySeries(): Record<SensorName, ChartPoint[]> {
  return { volt: [], rotate: [], pressure: [], vibration: [] };
}

function toChartSeries(t: TelemetrySeries): Record<SensorName, ChartPoint[]> {
  const out = emptySeries();
  for (const name of SENSOR_NAMES) out[name] = t[name] ?? [];
  return out;
}

export function merge(base: GraphView | null, extra: GraphView): GraphView {
  if (!base) return extra;
  const nodes = new Map([...base.nodes, ...extra.nodes].map((n) => [n.id, n]));
  const edges = new Map([...base.edges, ...extra.edges].map((e) => [e.id, e]));
  return { nodes: [...nodes.values()], edges: [...edges.values()], truncated: base.truncated || extra.truncated };
}
