import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import GlobalSearch from './GlobalSearch';

/**
 * The shortcut and the trigger (Phase 204).
 *
 * The binding is global and capture-phase, so the risk it carries is stealing a
 * keystroke that belongs to something else — most obviously Ctrl-K inside the
 * memo editor. These tests pin that boundary as much as they pin the feature.
 */

vi.mock('../../hooks/useAuth', () => ({ useAuth: () => ({ role: 'maker' }) }));
vi.mock('../../hooks/useWorkQueue', () => ({
  useWorkQueue: () => ({ allItems: [], act: vi.fn() }),
}));
vi.mock('../../services/api', () => ({
  default: { get: vi.fn().mockResolvedValue({ data: { results: [] } }) },
}));

const setup = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><GlobalSearch /></MemoryRouter>
    </QueryClientProvider>,
  );
};

const palette = () => screen.queryByRole('dialog');

beforeEach(() => vi.clearAllMocks());

describe('trigger', () => {
  it('is a button that says what it does and advertises the shortcut', () => {
    setup();
    const btn = screen.getByRole('button');
    expect(btn).toHaveAttribute('aria-haspopup', 'dialog');
    expect(btn).toHaveTextContent(/search/i);
    expect(btn).toHaveTextContent(/Ctrl K/);
  });

  it('opens the palette on click and reflects state in aria-expanded', async () => {
    setup();
    expect(palette()).toBeNull();
    expect(screen.getByRole('button')).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(screen.getByRole('button'));
    await waitFor(() => expect(palette()).toBeInTheDocument());
  });
});

describe('shortcut', () => {
  it('opens on Ctrl+K', async () => {
    setup();
    fireEvent.keyDown(document, { key: 'k', ctrlKey: true });
    await waitFor(() => expect(palette()).toBeInTheDocument());
  });

  it('opens on Cmd+K for macOS', async () => {
    setup();
    fireEvent.keyDown(document, { key: 'k', metaKey: true });
    await waitFor(() => expect(palette()).toBeInTheDocument());
  });

  it('accepts an uppercase K, which is what a held Shift produces', async () => {
    setup();
    fireEvent.keyDown(document, { key: 'K', ctrlKey: true });
    await waitFor(() => expect(palette()).toBeInTheDocument());
  });

  it('ignores a bare k, which is an ordinary character', () => {
    setup();
    fireEvent.keyDown(document, { key: 'k' });
    expect(palette()).toBeNull();
  });

  it('toggles shut on a second press', async () => {
    setup();
    fireEvent.keyDown(document, { key: 'k', ctrlKey: true });
    await waitFor(() => expect(palette()).toBeInTheDocument());
    fireEvent.keyDown(document, { key: 'k', ctrlKey: true });
    await waitFor(() => expect(palette()).toBeNull());
  });

  it('leaves Ctrl+K alone while the user is typing in a field', () => {
    // The memo editor binds Ctrl-K for its own link tool; stealing it globally
    // would break authoring, which matters more than opening search quickly.
    setup();
    const input = document.createElement('input');
    document.body.appendChild(input);
    input.focus();
    fireEvent.keyDown(input, { key: 'k', ctrlKey: true, bubbles: true });
    expect(palette()).toBeNull();
    input.remove();
  });

  it('leaves it alone in a contenteditable rich-text area', () => {
    setup();
    const editor = document.createElement('div');
    editor.setAttribute('contenteditable', 'true');
    document.body.appendChild(editor);
    fireEvent.keyDown(editor, { key: 'k', ctrlKey: true, bubbles: true });
    expect(palette()).toBeNull();
    editor.remove();
  });

  it('still CLOSES with the shortcut from inside its own input', async () => {
    // Once open, the palette's own field is a typing target - the guard must
    // not trap the user inside it.
    setup();
    fireEvent.keyDown(document, { key: 'k', ctrlKey: true });
    await waitFor(() => expect(palette()).toBeInTheDocument());
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'k', ctrlKey: true, bubbles: true });
    await waitFor(() => expect(palette()).toBeNull());
  });
});

describe('focus restore', () => {
  it('returns focus to the trigger when the palette closes', async () => {
    setup();
    const btn = screen.getByRole('button');
    btn.focus();
    fireEvent.click(btn);
    await waitFor(() => expect(screen.getByRole('combobox')).toHaveFocus());
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Escape' });
    await waitFor(() => expect(btn).toHaveFocus());
  });
});
