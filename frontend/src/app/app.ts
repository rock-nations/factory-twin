import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatBadgeModule } from '@angular/material/badge';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatSnackBar } from '@angular/material/snack-bar';
import { MatToolbarModule } from '@angular/material/toolbar';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { ChatStore } from './core/state/chat.store';
import { FactoryStore } from './core/state/factory.store';
import { HealthStore } from './core/state/health.store';
import { UiStore } from './core/state/ui.store';
import { ChatDrawer } from './features/chat/chat-drawer';
import { AlarmSnackbar } from './shared/alarm-snackbar';
import { StatusDot, Tone } from './shared/status-dot';

@Component({
  selector: 'app-root',
  imports: [
    RouterOutlet,
    RouterLink,
    RouterLinkActive,
    MatToolbarModule,
    MatSidenavModule,
    MatButtonModule,
    MatIconModule,
    MatBadgeModule,
    ChatDrawer,
    StatusDot,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class App {
  private readonly healthStore = inject(HealthStore);
  private readonly snack = inject(MatSnackBar);
  protected readonly ui = inject(UiStore);
  protected readonly chat = inject(ChatStore);
  protected readonly factory = inject(FactoryStore);

  protected readonly nav = [
    { path: '/dashboard', label: 'Dashboard', icon: 'dashboard' },
    { path: '/graph', label: 'Graph explorer', icon: 'hub' },
    { path: '/simulator', label: 'Simulator', icon: 'sensors' },
  ];

  protected readonly openAlarmCount = computed(() => this.factory.openAlarms().length);

  protected readonly backendTone = computed<Tone>(() => {
    switch (this.healthStore.backend().state) {
      case 'ok':
        return 'ok';
      case 'degraded':
        return 'warn';
      case 'unreachable':
        return 'error';
      default:
        return 'idle';
    }
  });

  protected readonly backendLabel = computed(() => {
    const { state, health } = this.healthStore.backend();
    if (state === 'unreachable') return 'Backend unreachable';
    if (state === 'checking' || !health) return 'Checking…';
    return `Neo4j ${health.neo4j.status} · MQTT ${health.mqtt.status}`;
  });

  constructor() {
    this.factory.init();
    this.chat.init();
    this.factory.alarmRaised$.pipe(takeUntilDestroyed()).subscribe((event) => {
      this.snack.openFromComponent(AlarmSnackbar, {
        data: event,
        duration: 12_000,
        horizontalPosition: 'center',
        verticalPosition: 'top',
        panelClass: 'alarm-toast',
      });
    });
  }

  protected toggleChat(): void {
    this.ui.toggleChat();
    if (this.ui.chatOpen()) this.chat.unread.set(0);
  }
}
