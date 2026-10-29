import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ChatMessage, Trace } from '../../core/models/chat';
import { ChatMessageView, renderMarkdown } from './chat-message';

const trace: Trace = {
  route: 'hybrid',
  rounds: 1,
  total_latency_ms: 1234,
  input_tokens: 900,
  output_tokens: 120,
  cost_usd: 0.0042,
  step_latency_ms: { orchestrate: 300, tools: 400, answer: 534 },
  llm_calls: [{ step: 'answer', model: 'm', input_tokens: 900, output_tokens: 120, cached_tokens: 0, cost_usd: 0.004, latency_ms: 534 }],
  tools: [
    {
      name: 'query_twin_data', arguments: { question: 'open alarms on A2' }, summary: '2 row(s)',
      cypher: 'MATCH (m:Machine) RETURN m.id AS machine', row_count: 2, truncated: false,
      preview_rows: [{ machine: 'M017' }, { machine: 'M018' }],
      sources: [{ ref: 1, kind: 'cypher', title: 'Graph query result', doc_id: null, doc_type: null }],
      retrieval_scores: [], latency_ms: 80, error: null,
    },
    {
      name: 'search_knowledge_base', arguments: { query: 'spindle bearing' }, summary: '2 chunk(s) from 1 document(s)',
      cypher: null, row_count: null, truncated: false, preview_rows: [],
      sources: [{ ref: 2, kind: 'document', title: 'SOP: Replacing the spindle bearing', doc_id: 'sop-comp1', doc_type: 'sop' }],
      retrieval_scores: [0.91, 0.84], latency_ms: 120, error: null,
    },
  ],
};

function render(message: Partial<ChatMessage>): ComponentFixture<ChatMessageView> {
  const fixture = TestBed.createComponent(ChatMessageView);
  fixture.componentRef.setInput('message', { id: 1, role: 'assistant', text: '', ...message });
  fixture.detectChanges();
  return fixture;
}
const el = (f: ComponentFixture<unknown>) => f.nativeElement as HTMLElement;

describe('renderMarkdown', () => {
  it('renders tables and links machine ids', () => {
    const html = renderMarkdown('| machine | status |\n|---|---|\n| M017 | alarm |');
    expect(html).toContain('<table>');
    expect(html).toContain('<a href="/machines/M017">M017</a>');
  });
});

describe('ChatMessageView', () => {
  beforeEach(() => TestBed.configureTestingModule({ providers: [provideRouter([])] }));

  it('renders user text as plain text, never as HTML', () => {
    const f = render({ role: 'user', text: '<img src=x onerror=alert(1)> M017' });
    expect(el(f).querySelector('img')).toBeNull();
    expect(el(f).textContent).toContain('<img src=x');
  });

  it('renders assistant markdown with a table and sanitises scripts', () => {
    const f = render({ text: '**M017** is hot\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n<script>alert(1)</script>' });
    const md = el(f).querySelector('[data-testid="markdown"]')!;
    expect(md.querySelector('strong')?.textContent).toBe('M017');
    expect(md.querySelectorAll('td')).toHaveLength(2);
    expect(md.querySelector('script')).toBeNull();
  });

  it('turns machine ids into links that navigate inside the app', async () => {
    const f = render({ text: 'Check M017 now' });
    const router = TestBed.inject(Router);
    const nav = vi.spyOn(router, 'navigateByUrl').mockResolvedValue(true);
    const link = el(f).querySelector('a[href="/machines/M017"]') as HTMLAnchorElement;
    expect(link.textContent).toBe('M017');
    const click = new MouseEvent('click', { bubbles: true, cancelable: true });
    link.dispatchEvent(click);
    expect(click.defaultPrevented).toBe(true);
    expect(nav).toHaveBeenCalledWith('/machines/M017');
  });

  it('shows live tool steps and a thinking hint while streaming', () => {
    const f = render({
      streaming: true, route: 'hybrid',
      steps: [
        { name: 'get_machine_context', args: {}, done: true, summary: 'M017: 1 open alarm(s)' },
        { name: 'find_similar_incidents', args: {}, done: false },
      ],
    });
    const steps = el(f).querySelectorAll('[data-testid="live-steps"] li');
    expect(steps).toHaveLength(2);
    expect(steps[0].textContent).toContain('Reading machine context');
    expect(steps[0].textContent).toContain('M017: 1 open alarm(s)');
    expect(steps[1].classList.contains('done')).toBe(false);
    expect(el(f).textContent).toContain('Thinking…');
    expect(el(f).querySelector('[data-testid="route-badge"]')?.textContent).toContain('Hybrid GraphRAG');
  });

  it('emits proactive quick actions', () => {
    const f = render({ role: 'proactive', text: '⚠️ M017 vibration high', actions: [{ label: 'Diagnose M017', message: 'Diagnose M017: vibration alarm' }] });
    const got: string[] = [];
    f.componentInstance.action.subscribe((m) => got.push(m));
    (el(f).querySelector('.actions button') as HTMLButtonElement).click();
    expect(got).toEqual(['Diagnose M017: vibration alarm']);
    expect(el(f).textContent).toContain('Alert');
  });

  it('shows the "How I answered" panel once the answer is done, collapsed by default', () => {
    const f = render({ text: 'Answer', trace });
    const details = el(f).querySelector('details[data-testid="trace-panel"]') as HTMLDetailsElement;
    expect(details).not.toBeNull();
    expect(details.open).toBe(false);
    expect(details.querySelector('summary')?.textContent).toContain('How I answered');
  });

  it('has no trace panel for a message without a trace', () => {
    expect(el(render({ text: 'hi' })).querySelector('[data-testid="trace-panel"]')).toBeNull();
  });
});

describe('TracePanel (inside a message)', () => {
  beforeEach(() => TestBed.configureTestingModule({ providers: [provideRouter([])] }));

  it('lists route, tools in order, cypher, results preview, sources, latency and cost', () => {
    const f = render({ text: 'Answer', trace });
    const text = el(f).querySelector('[data-testid="trace-panel"]')!.textContent!;
    expect(el(f).querySelector('[data-testid="trace-panel"] [data-testid="route-badge"]')?.textContent).toContain('Hybrid GraphRAG');
    expect(text.indexOf('query_twin_data')).toBeLessThan(text.indexOf('search_knowledge_base'));
    expect(el(f).querySelector('[data-testid="cypher"]')?.textContent).toBe('MATCH (m:Machine) RETURN m.id AS machine');
    const rows = el(f).querySelectorAll('.tablewrap tbody tr');
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain('M017');
    expect(text).toContain('[2] SOP: Replacing the spindle bearing');
    expect(text).toContain('sop');
    expect(text).toContain('0.91, 0.84');
    expect(text).toContain('1234 ms');
    expect(text).toContain('$0.0042');
    expect(text).toContain('900 in / 120 out');
  });

  it('previews at most the rows it was given and says how many exist', () => {
    const many = { ...trace, tools: [{ ...trace.tools[0], row_count: 50, preview_rows: Array.from({ length: 10 }, (_, i) => ({ machine: `M${100 + i}` })) }] };
    const f = render({ text: 'a', trace: many });
    expect(el(f).querySelectorAll('.tablewrap tbody tr')).toHaveLength(10);
    expect(el(f).textContent).toContain('showing 10 of 50 rows');
  });

  it('copies the Cypher to the clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const f = render({ text: 'a', trace });
    (el(f).querySelector('.copy') as HTMLButtonElement).click();
    await f.whenStable();
    expect(writeText).toHaveBeenCalledWith('MATCH (m:Machine) RETURN m.id AS machine');
  });

  it('says so when no tools were used', () => {
    const f = render({ text: 'hi', trace: { ...trace, route: 'direct', tools: [] } });
    expect(el(f).textContent).toContain('No tools were needed');
  });
});
