import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { App } from './app';
import { SOCKET_FACTORY } from './core/services/reconnecting-socket';
import { fakeSocketFactory } from './testing';

// the health poll starts on a 0ms timer
const tick = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

describe('App shell', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideRouter([]),
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: SOCKET_FACTORY, useValue: fakeSocketFactory },
      ],
    }).compileComponents();
  });

  it('shows backend health from /api/health', async () => {
    const fixture = TestBed.createComponent(App);
    const http = TestBed.inject(HttpTestingController);
    fixture.detectChanges();
    await tick();

    http.expectOne('/api/health').flush({
      status: 'ok',
      neo4j: { status: 'ok', detail: null },
      mqtt: { status: 'ok', detail: null },
    });
    await fixture.whenStable();
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).querySelector(
      '[data-testid="backend-health"]',
    )?.textContent;
    expect(text).toContain('Neo4j ok');
    expect(text).toContain('MQTT ok');
  });

  it('reports an unreachable backend', async () => {
    const fixture = TestBed.createComponent(App);
    const http = TestBed.inject(HttpTestingController);
    fixture.detectChanges();
    await tick();

    http.expectOne('/api/health').error(new ProgressEvent('error'));
    await fixture.whenStable();
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).textContent).toContain('Backend unreachable');
  });
});
