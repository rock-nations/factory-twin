import { Injectable, inject } from '@angular/core';

import { ChatServerMessage } from '../models/chat';
import { ReconnectingSocket, SOCKET_FACTORY } from './reconnecting-socket';

@Injectable({ providedIn: 'root' })
export class ChatSocketService {
  private readonly socket = new ReconnectingSocket<ChatServerMessage>('/ws/chat', inject(SOCKET_FACTORY));
  readonly messages$ = this.socket.messages$;
  readonly status = this.socket.status;

  connect(): void {
    this.socket.connect();
  }

  /** Returns false when the socket is not open (the caller shows an error instead of losing the message). */
  sendUserMessage(sessionId: string, message: string): boolean {
    return this.socket.send({ type: 'user_message', session_id: sessionId, message });
  }
}
