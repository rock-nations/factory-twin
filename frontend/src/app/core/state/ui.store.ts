import { Injectable, signal } from '@angular/core';

export const CHAT_MIN_WIDTH = 340;
export const CHAT_DEFAULT_WIDTH = 480;
export const CHAT_WIDE_WIDTH = 800;
/** the page content always keeps at least this much room next to the open chat */
const CONTENT_MIN_WIDTH = 480;
const NAV_WIDTH = 208;
const WIDTH_KEY = 'ft.chat.width';
const THEME_KEY = 'ft.theme';
/** below this window width the chat does not open by itself, it would cover the page */
const CHAT_AUTO_OPEN_MIN_VIEWPORT = 1100;

export type Theme = 'light' | 'dark';

function loadTheme(): Theme {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === 'light' || saved === 'dark') return saved;
  } catch {
    // storage can be unavailable (private mode); use the default
  }
  return 'light';
}

function loadWidth(): number {
  try {
    const saved = Number(localStorage.getItem(WIDTH_KEY));
    if (Number.isFinite(saved) && saved >= CHAT_MIN_WIDTH) return saved;
  } catch {
    // storage can be unavailable (private mode); use the default
  }
  return CHAT_DEFAULT_WIDTH;
}

@Injectable({ providedIn: 'root' })
export class UiStore {
  /** the chat is open when someone arrives, as long as the window is wide enough for it */
  readonly chatOpen = signal(window.innerWidth >= CHAT_AUTO_OPEN_MIN_VIEWPORT);
  readonly theme = signal<Theme>(loadTheme());
  /** chat panel width in px; resized by dragging its left edge or the expand button */
  readonly chatWidth = signal(loadWidth());

  constructor() {
    this.applyTheme(this.theme());
  }

  toggleTheme(): void {
    const next: Theme = this.theme() === 'light' ? 'dark' : 'light';
    this.theme.set(next);
    this.applyTheme(next);
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      // ignore
    }
  }

  private applyTheme(theme: Theme): void {
    document.documentElement.style.colorScheme = theme;
  }

  toggleChat(): void {
    this.chatOpen.update((open) => !open);
  }

  openChat(): void {
    this.chatOpen.set(true);
  }

  /** Clamps to the minimum and to what the window leaves free, then remembers it. */
  setChatWidth(px: number, viewport = window.innerWidth): void {
    const max = Math.max(CHAT_MIN_WIDTH, viewport - NAV_WIDTH - CONTENT_MIN_WIDTH);
    const width = Math.round(Math.min(max, Math.max(CHAT_MIN_WIDTH, px)));
    this.chatWidth.set(width);
    try {
      localStorage.setItem(WIDTH_KEY, String(width));
    } catch {
      // ignore
    }
  }

  /** Switches between the default and the wide layout. */
  toggleChatWide(viewport = window.innerWidth): void {
    this.setChatWidth(this.chatWidth() > CHAT_DEFAULT_WIDTH + 40 ? CHAT_DEFAULT_WIDTH : CHAT_WIDE_WIDTH, viewport);
  }
}
