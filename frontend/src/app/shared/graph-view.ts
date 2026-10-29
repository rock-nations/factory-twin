import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  OnDestroy,
  effect,
  inject,
  input,
  output,
  viewChild,
} from '@angular/core';
import cytoscape, { Core, ElementDefinition } from 'cytoscape';

import { GraphView } from '../core/models/factory';
import { Theme, UiStore } from '../core/state/ui.store';

export const LABEL_COLORS: Record<string, string> = {
  Plant: '#a371f7',
  ProductionLine: '#8957e5',
  Machine: '#58a6ff',
  MachineModel: '#388bfd',
  Component: '#39c5cf',
  Sensor: '#2ea043',
  Alarm: '#f85149',
  Failure: '#db6d28',
  MaintenanceEvent: '#d29922',
  ErrorType: '#e3b341',
  ErrorEvent: '#bf8700',
  Technician: '#8b949e',
  Document: '#f778ba',
  Chunk: '#bc4c9a',
};

function graphStyle(theme: Theme): cytoscape.StylesheetJson {
  const dark = theme === 'dark';
  const text = dark ? '#c9d1d9' : '#24292f';
  const outline = dark ? '#0e1116' : '#f4f6f9';
  const edge = dark ? '#3b4452' : '#a8b2c0';
  const edgeText = dark ? '#6e7681' : '#59636f';
  const selected = dark ? '#fff' : '#24292f';
  return [
    {
      selector: 'node',
      style: {
        label: 'data(caption)',
        'background-color': 'data(color)',
        color: text,
        'font-size': 9,
        'text-valign': 'bottom',
        'text-margin-y': 3,
        'text-outline-color': outline,
        'text-outline-width': 2,
        width: 16,
        height: 16,
      },
    },
    { selector: 'node[label = "Machine"]', style: { width: 24, height: 24 } },
    { selector: 'node.selected', style: { 'border-width': 3, 'border-color': selected } },
    {
      selector: 'edge',
      style: {
        width: 1,
        'line-color': edge,
        'target-arrow-color': edge,
        'target-arrow-shape': 'triangle',
        'curve-style': 'bezier',
        label: 'data(type)',
        'font-size': 7,
        color: edgeText,
        'text-rotation': 'autorotate',
      },
    },
  ];
}

@Component({
  selector: 'app-graph-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<div #host class="host" data-testid="graph-canvas"></div>`,
  styles: `
    :host { display: block; position: relative; }
    .host { position: absolute; inset: 0; }
  `,
})
export class GraphViewComponent implements OnDestroy {
  readonly graph = input<GraphView | null>(null);
  readonly selectedId = input<string | null>(null);
  readonly nodeSelect = output<string>();
  readonly nodeExpand = output<string>();

  private readonly host = viewChild.required<ElementRef<HTMLDivElement>>('host');
  private readonly ui = inject(UiStore);
  private cy: Core | null = null;

  constructor() {
    effect(() => {
      const theme = this.ui.theme();
      this.cy?.style(graphStyle(theme));
    });
    effect(() => {
      const graph = this.graph();
      if (!graph) return;
      this.render(graph);
    });
    effect(() => {
      const id = this.selectedId();
      if (!this.cy) return;
      this.cy.nodes().removeClass('selected');
      if (id) this.cy.getElementById(id).addClass('selected');
    });
  }

  ngOnDestroy(): void {
    this.cy?.destroy();
  }

  private ensure(): Core {
    if (this.cy) return this.cy;
    this.cy = cytoscape({
      container: this.host().nativeElement,
      wheelSensitivity: 0.3,
      style: graphStyle(this.ui.theme()),
    });
    this.cy.on('tap', 'node', (e) => this.nodeSelect.emit(e.target.id()));
    this.cy.on('dbltap', 'node', (e) => this.nodeExpand.emit(e.target.id()));
    return this.cy;
  }

  private render(graph: GraphView): void {
    const cy = this.ensure();
    const elements: ElementDefinition[] = [
      ...graph.nodes.map((n) => ({
        group: 'nodes' as const,
        data: { id: n.id, label: n.label, caption: n.caption, color: LABEL_COLORS[n.label] ?? '#8b949e' },
      })),
      ...graph.edges.map((e) => ({
        group: 'edges' as const,
        data: { id: e.id, source: e.source, target: e.target, type: e.type },
      })),
    ];
    cy.elements().remove();
    cy.add(elements);
    cy.layout({ name: 'cose', animate: false, nodeRepulsion: () => 9000, idealEdgeLength: () => 70, fit: true, padding: 24 }).run();
  }
}
