import { ChangeDetectionStrategy, Component, computed, inject, input } from '@angular/core';
import type { EChartsCoreOption } from 'echarts/core';
import { NgxEchartsDirective } from 'ngx-echarts';

import { UiStore } from '../core/state/ui.store';

export interface ChartPoint {
  ts: string;
  value: number;
}

@Component({
  selector: 'app-line-chart',
  imports: [NgxEchartsDirective],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<div echarts class="chart" [options]="options()" [merge]="options()"></div>`,
  styles: `
    :host { display: block; }
    .chart { width: 100%; height: 100%; min-height: 160px; }
  `,
})
export class LineChart {
  private readonly ui = inject(UiStore);
  readonly points = input<ChartPoint[]>([]);
  readonly unit = input('');
  readonly warnHigh = input<number | null>(null);
  readonly critHigh = input<number | null>(null);
  readonly warnLow = input<number | null>(null);
  readonly critLow = input<number | null>(null);

  protected readonly options = computed<EChartsCoreOption>(() => {
    const dark = this.ui.theme() === 'dark';
    const axis = dark ? '#2a323d' : '#c4ccd6';
    const grid = dark ? '#1c232c' : '#e6eaf0';
    const label = dark ? '#8b97a7' : '#59636f';
    const warn = dark ? '#d29922' : '#a86a00';
    const crit = dark ? '#f85149' : '#cf222e';
    const line = dark ? '#58a6ff' : '#0969da';
    const marks = [
      ...(this.warnHigh() !== null ? [{ yAxis: this.warnHigh()!, lineStyle: { color: warn }, label: { formatter: 'warn' } }] : []),
      ...(this.critHigh() !== null ? [{ yAxis: this.critHigh()!, lineStyle: { color: crit }, label: { formatter: 'crit' } }] : []),
      ...(this.warnLow() !== null ? [{ yAxis: this.warnLow()!, lineStyle: { color: warn }, label: { formatter: 'warn' } }] : []),
      ...(this.critLow() !== null ? [{ yAxis: this.critLow()!, lineStyle: { color: crit }, label: { formatter: 'crit' } }] : []),
    ];
    const values = this.points().map((p) => p.value);
    const limits = [this.warnHigh(), this.critHigh(), this.warnLow(), this.critLow()].filter((v): v is number => v !== null);
    const all = [...values, ...limits];
    return {
      animation: false,
      grid: { left: 44, right: 44, top: 12, bottom: 22 },
      tooltip: { trigger: 'axis', valueFormatter: (v: number) => `${v.toFixed(1)} ${this.unit()}` },
      xAxis: {
        type: 'category',
        data: this.points().map((p) => p.ts.slice(11, 19)),
        axisLine: { lineStyle: { color: axis } },
        axisLabel: { color: label, fontSize: 10 },
      },
      yAxis: {
        type: 'value',
        min: all.length ? Math.floor(Math.min(...all) - 2) : undefined,
        max: all.length ? Math.ceil(Math.max(...all) + 2) : undefined,
        splitLine: { lineStyle: { color: grid } },
        axisLabel: { color: label, fontSize: 10 },
      },
      series: [
        {
          type: 'line',
          data: values,
          showSymbol: false,
          smooth: false,
          lineStyle: { width: 1.5, color: line },
          markLine: { silent: true, symbol: 'none', data: marks },
        },
      ],
    };
  });
}
