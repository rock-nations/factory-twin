import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { CHAT_DEFAULT_WIDTH, CHAT_MIN_WIDTH, CHAT_WIDE_WIDTH, UiStore } from './ui.store';

describe('UiStore chat width', () => {
  let ui: UiStore;

  beforeEach(() => {
    localStorage.clear();
    ui = TestBed.inject(UiStore);
  });

  it('starts at the default width', () => {
    expect(ui.chatWidth()).toBe(CHAT_DEFAULT_WIDTH);
  });

  it('never goes below the minimum', () => {
    ui.setChatWidth(50, 1600);
    expect(ui.chatWidth()).toBe(CHAT_MIN_WIDTH);
  });

  it('leaves room for the nav and the page content', () => {
    ui.setChatWidth(5000, 1200);
    expect(ui.chatWidth()).toBe(1200 - 208 - 480);
  });

  it('keeps the minimum even in a tiny window', () => {
    ui.setChatWidth(900, 500);
    expect(ui.chatWidth()).toBe(CHAT_MIN_WIDTH);
  });

  it('remembers the width', () => {
    ui.setChatWidth(620, 1600);
    expect(localStorage.getItem('ft.chat.width')).toBe('620');
  });

  it('toggles between default and wide', () => {
    ui.toggleChatWide(2000);
    expect(ui.chatWidth()).toBe(CHAT_WIDE_WIDTH);
    ui.toggleChatWide(2000);
    expect(ui.chatWidth()).toBe(CHAT_DEFAULT_WIDTH);
  });
});

describe('UiStore theme and chat', () => {
  beforeEach(() => localStorage.clear());

  it('defaults to the light theme and applies it to the page', () => {
    const ui = TestBed.inject(UiStore);
    expect(ui.theme()).toBe('light');
    expect(document.documentElement.style.colorScheme).toBe('light');
  });

  it('toggles to dark, applies it and remembers it', () => {
    const ui = TestBed.inject(UiStore);
    ui.toggleTheme();
    expect(ui.theme()).toBe('dark');
    expect(document.documentElement.style.colorScheme).toBe('dark');
    expect(localStorage.getItem('ft.theme')).toBe('dark');
  });

  it('opens the chat by itself only in a wide window', () => {
    const original = window.innerWidth;
    try {
      Object.defineProperty(window, 'innerWidth', { value: 1400, configurable: true });
      TestBed.resetTestingModule();
      expect(TestBed.inject(UiStore).chatOpen()).toBe(true);
      Object.defineProperty(window, 'innerWidth', { value: 800, configurable: true });
      TestBed.resetTestingModule();
      expect(TestBed.inject(UiStore).chatOpen()).toBe(false);
    } finally {
      Object.defineProperty(window, 'innerWidth', { value: original, configurable: true });
    }
  });
});
