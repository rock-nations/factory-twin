import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';

import { SAMPLE_CATEGORIES, SampleCategory, SampleCategoryId } from '../../core/models/sample-questions';

/** Categorised example questions to try the copilot by hand. */
@Component({
  selector: 'app-sample-questions',
  imports: [MatIconModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './sample-questions.html',
  styleUrl: './sample-questions.scss',
})
export class SampleQuestions {
  readonly disabled = input(false);
  /** send the question straight away */
  readonly ask = output<string>();
  /** put the question into the composer so it can be edited first */
  readonly edit = output<string>();

  protected readonly categories = SAMPLE_CATEGORIES;
  protected readonly selectedId = signal<SampleCategoryId>(SAMPLE_CATEGORIES[0].id);
  protected readonly selected = computed<SampleCategory>(
    () => this.categories.find((c) => c.id === this.selectedId()) ?? this.categories[0],
  );
}
