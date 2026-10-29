import { Injectable, inject } from '@angular/core';

import { LiveEvent } from '../models/events';
import { ReconnectingSocket, SOCKET_FACTORY } from './reconnecting-socket';

@Injectable({ providedIn: 'root' })
export class EventsSocketService {
  private readonly socket = new ReconnectingSocket<LiveEvent>('/ws/events', inject(SOCKET_FACTORY));
  readonly events$ = this.socket.messages$;
  readonly status = this.socket.status;

  connect(): void {
    this.socket.connect();
  }
}
