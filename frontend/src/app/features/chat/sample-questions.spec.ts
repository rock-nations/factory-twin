import { ComponentFixture, TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { SAMPLE_CATEGORIES } from '../../core/models/sample-questions';
import { SampleQuestions } from './sample-questions';

describe('SampleQuestions', () => {
  let fixture: ComponentFixture<SampleQuestions>;
  const el = () => fixture.nativeElement as HTMLElement;
  const click = (selector: string, index = 0) => {
    (el().querySelectorAll<HTMLElement>(selector)[index]).click();
    fixture.detectChanges();
  };

  beforeEach(() => {
    fixture = TestBed.createComponent(SampleQuestions);
    fixture.detectChanges();
  });

  it('has a tab per category and shows the first category by default', () => {
    expect(el().querySelectorAll('[role="tab"]').length).toBe(SAMPLE_CATEGORIES.length);
    expect(el().querySelectorAll('[data-testid="sample-ask"]').length).toBe(SAMPLE_CATEGORIES[0].questions.length);
  });

  it('switches the question list when another tab is chosen', () => {
    click('[data-testid="sample-tab-rag"]');
    const rag = SAMPLE_CATEGORIES.find((c) => c.id === 'rag')!;
    const first = el().querySelector('[data-testid="sample-ask"] .text')!;
    expect(first.textContent).toContain(rag.questions[0].text);
    expect(el().querySelector('.tab.active')?.textContent).toContain('Vector RAG');
  });

  it('emits ask with the question text on click and edit on the pencil', () => {
    const asked: string[] = [];
    const edited: string[] = [];
    fixture.componentInstance.ask.subscribe((q) => asked.push(q));
    fixture.componentInstance.edit.subscribe((q) => edited.push(q));
    click('[data-testid="sample-ask"]', 1);
    click('[data-testid="sample-edit"]', 2);
    expect(asked).toEqual([SAMPLE_CATEGORIES[0].questions[1].text]);
    expect(edited).toEqual([SAMPLE_CATEGORIES[0].questions[2].text]);
  });

  it('numbers the steps of an ordered category and disables asking when told to', () => {
    click('[data-testid="sample-tab-followup"]');
    expect(el().querySelectorAll('.step').length).toBe(3);
    fixture.componentRef.setInput('disabled', true);
    fixture.detectChanges();
    expect(el().querySelector<HTMLButtonElement>('[data-testid="sample-ask"]')!.disabled).toBe(true);
  });

  it('keeps every question unique and non-empty', () => {
    const all = SAMPLE_CATEGORIES.flatMap((c) => c.questions);
    expect(all.every((q) => q.text.trim() && q.expect.trim())).toBe(true);
    expect(new Set(all.map((q) => q.text)).size).toBe(all.length);
  });
});
