import React, { useState } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act, waitFor, fireEvent } from '@testing-library/react';

import { useDocumentDraft, IDLE_MS, INTERVAL_MS, STATUS } from './useDocumentDraft';

vi.mock('../services/draftService', () => ({
  draftService: {
    saveDraft: vi.fn(() => Promise.resolve({ version: 1, saved_at: '2026-08-26T10:00:00Z' })),
    getDraft: vi.fn(() => Promise.resolve(null)),
    discardDraft: vi.fn(() => Promise.resolve()),
    markRecovered: vi.fn(() => Promise.resolve()),
  },
}));
vi.mock('../services/draftStore', () => ({
  putSnapshot: vi.fn(() => Promise.resolve(true)),
  getSnapshot: vi.fn(() => Promise.resolve(null)),
  deleteSnapshot: vi.fn(() => Promise.resolve(true)),
  clearUser: vi.fn(() => Promise.resolve(0)),
  listUser: vi.fn(() => Promise.resolve([])),
  isSupported: () => true,
}));

const { draftService } = await import('../services/draftService');
const store = await import('../services/draftStore');

/** A minimal form driving the engine, so the test exercises it as a form does. */
const Harness = ({ initial = '', ...opts }) => {
  const [text, setText] = useState(initial);
  const draft = useDocumentDraft({
    kind: 'memo', documentKey: 'new', userId: 'u1',
    payload: { form: { subject: text } }, ...opts,
  });
  return (
    <div>
      <input aria-label="subject" value={text} onChange={(e) => setText(e.target.value)} />
      <span data-testid="status">{draft.status}</span>
      <span data-testid="unsaved">{String(draft.unsaved)}</span>
      <button type="button" onClick={draft.saveNow}>save now</button>
      <button type="button" onClick={draft.complete}>complete</button>
      <button type="button" onClick={draft.discard}>discard</button>
      {draft.recoverable && <span data-testid="recoverable">offer</span>}
    </div>
  );
};

/* fireEvent.change goes through React's native value setter; assigning
   input.value directly does not register with a controlled component, which
   would make every "the user typed" assertion below pass vacuously. */
const type = async (value) => {
  await act(async () => {
    fireEvent.change(screen.getByLabelText('subject'), { target: { value } });
  });
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ shouldAdvanceTime: true });
  draftService.getDraft.mockResolvedValue(null);
  draftService.saveDraft.mockResolvedValue({ version: 1, saved_at: '2026-08-26T10:00:00Z' });
  store.getSnapshot.mockResolvedValue(null);
  store.putSnapshot.mockResolvedValue(true);
});
afterEach(() => vi.useRealTimers());

describe('autosave triggers (Phase 111.3)', () => {
  it('saves five seconds after the user stops typing', async () => {
    render(<Harness />);
    await type('Server refresh');
    expect(draftService.saveDraft).not.toHaveBeenCalled();

    await act(async () => { vi.advanceTimersByTime(IDLE_MS); });
    await waitFor(() => expect(draftService.saveDraft).toHaveBeenCalledTimes(1));
  });

  it('does not save while the user is still typing', async () => {
    render(<Harness />);
    for (const value of ['S', 'Se', 'Ser', 'Serv']) {
      await type(value);
      await act(async () => { vi.advanceTimersByTime(IDLE_MS - 1000); });
    }
    expect(draftService.saveDraft).not.toHaveBeenCalled();
  });

  it('saves on the interval even when typing never pauses', async () => {
    render(<Harness />);
    // Keep resetting the idle timer, so only the interval can fire.
    for (let i = 0; i < 8; i += 1) {
      await type(`continuous ${i}`);
      await act(async () => { vi.advanceTimersByTime(IDLE_MS - 1000); });
    }
    await waitFor(() => expect(draftService.saveDraft).toHaveBeenCalled());
    // An interval save is a retained milestone (Decision 3).
    const milestones = draftService.saveDraft.mock.calls.map((c) => c[3]?.milestone);
    expect(milestones).toContain('interval');
  });

  it('saves when the tab is hidden', async () => {
    render(<Harness />);
    await type('switching away');
    await act(async () => {
      Object.defineProperty(document, 'visibilityState',
        { value: 'hidden', configurable: true });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await waitFor(() => expect(draftService.saveDraft).toHaveBeenCalled());
    Object.defineProperty(document, 'visibilityState',
      { value: 'visible', configurable: true });
  });

  it('does not re-send an unchanged snapshot', async () => {
    render(<Harness />);
    await type('once');
    await act(async () => { vi.advanceTimersByTime(IDLE_MS); });
    await waitFor(() => expect(draftService.saveDraft).toHaveBeenCalledTimes(1));

    await act(async () => { vi.advanceTimersByTime(INTERVAL_MS * 2); });
    expect(draftService.saveDraft).toHaveBeenCalledTimes(1);
  });

  it('is inert while the form is still loading', async () => {
    render(<Harness enabled={false} />);
    await type('too early');
    await act(async () => { vi.advanceTimersByTime(INTERVAL_MS); });
    expect(draftService.saveDraft).not.toHaveBeenCalled();
  });
});

describe('the local copy (Phase 111.6)', () => {
  it('writes locally as well as to the server', async () => {
    render(<Harness />);
    await type('both tiers');
    await act(async () => { vi.advanceTimersByTime(IDLE_MS); });
    await waitFor(() => expect(store.putSnapshot).toHaveBeenCalled());
    expect(draftService.saveDraft).toHaveBeenCalled();
  });

  it('never writes a confidential memo to local storage', async () => {
    render(<Harness confidential />);
    await type('restricted content');
    await act(async () => { vi.advanceTimersByTime(IDLE_MS); });
    await waitFor(() => expect(draftService.saveDraft).toHaveBeenCalled());
    // The server copy is authenticated storage; the local one is not.
    expect(store.putSnapshot).not.toHaveBeenCalled();
  });
});

describe('status reporting (Phase 111.8)', () => {
  it('reports Saved after a successful save', async () => {
    render(<Harness />);
    await type('ok');
    await act(async () => { vi.advanceTimersByTime(IDLE_MS); });
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent(STATUS.SAVED));
  });

  it('reports Offline when the server is unreachable but local worked', async () => {
    draftService.saveDraft.mockRejectedValue({ message: 'Network Error' });
    render(<Harness />);
    await type('no network');
    await act(async () => { vi.advanceTimersByTime(IDLE_MS); });
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent(STATUS.OFFLINE));
  });

  it('reports Local only when the server AND local storage both fail', async () => {
    draftService.saveDraft.mockRejectedValue({ message: 'Network Error' });
    store.putSnapshot.mockResolvedValue(false);
    render(<Harness />);
    await type('nowhere safe');
    await act(async () => { vi.advanceTimersByTime(IDLE_MS); });
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent(STATUS.LOCAL_ONLY));
  });

  it('never reports a document workflow status', async () => {
    /* Phase 111.4 as approved: autosave state is separate from document state. */
    render(<Harness />);
    await type('x');
    await act(async () => { vi.advanceTimersByTime(IDLE_MS); });
    await waitFor(() => expect(screen.getByTestId('status')).not.toHaveTextContent('draft'));
    expect(Object.values(STATUS)).toEqual(
      ['idle', 'saving', 'saved', 'offline', 'local-only'],
    );
  });
});

describe('offline sync (Phase 111.9)', () => {
  it('flushes what was typed offline as soon as the connection returns', async () => {
    const onLine = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    render(<Harness />);
    await type('written while offline');
    await act(async () => { vi.advanceTimersByTime(IDLE_MS); });
    await waitFor(() => expect(store.putSnapshot).toHaveBeenCalled());
    expect(draftService.saveDraft).not.toHaveBeenCalled();

    onLine.mockReturnValue(true);
    await act(async () => { window.dispatchEvent(new Event('online')); });
    await waitFor(() => expect(draftService.saveDraft).toHaveBeenCalled());
    onLine.mockRestore();
  });

  it('stops retrying a snapshot the server has refused as too large', async () => {
    draftService.saveDraft.mockRejectedValue({ response: { status: 413 } });
    render(<Harness />);
    await type('enormous');
    await act(async () => { vi.advanceTimersByTime(IDLE_MS); });
    await waitFor(() => expect(draftService.saveDraft).toHaveBeenCalledTimes(1));

    await act(async () => { window.dispatchEvent(new Event('online')); });
    // A retry cannot help, and looping on it would burn the connection.
    expect(draftService.saveDraft).toHaveBeenCalledTimes(1);
  });
});

describe('recovery (Phase 111.5)', () => {
  it('offers a server snapshot found on mount', async () => {
    draftService.getDraft.mockResolvedValue({
      payload: { form: { subject: 'Recovered' } },
      saved_at: '2026-08-26T09:00:00Z', versions: [],
    });
    render(<Harness />);
    await waitFor(() => expect(screen.getByTestId('recoverable')).toBeInTheDocument());
  });

  it('prefers the local copy when it is newer than the server one', async () => {
    /* The crash window: local is written on every change, the server waits. */
    draftService.getDraft.mockResolvedValue({
      payload: { form: { subject: 'server' } },
      saved_at: '2026-08-26T09:00:00Z', versions: [],
    });
    store.getSnapshot.mockResolvedValue({
      payload: { form: { subject: 'local, newer' } },
      savedAt: '2026-08-26T09:30:00Z',
    });

    let captured = null;
    const Capture = () => {
      const d = useDocumentDraft({
        kind: 'memo', documentKey: 'new', userId: 'u1', payload: { form: {} },
      });
      captured = d.recoverable;
      return <span>{d.recoverable ? 'yes' : 'no'}</span>;
    };
    render(<Capture />);
    await waitFor(() => expect(captured).not.toBeNull());
    expect(captured.source).toBe('local');
    expect(captured.payload.form.subject).toBe('local, newer');
  });

  it('offers nothing when there is nothing to recover', async () => {
    render(<Harness />);
    await act(async () => { vi.advanceTimersByTime(100); });
    expect(screen.queryByTestId('recoverable')).toBeNull();
  });
});

describe('completion and discard', () => {
  it('removes both copies once the document is really saved', async () => {
    render(<Harness />);
    await act(async () => { screen.getByText('complete').click(); });
    await waitFor(() => expect(store.deleteSnapshot).toHaveBeenCalled());
    expect(draftService.discardDraft).toHaveBeenCalledWith(
      'memo', 'new', { submitted: true },
    );
  });

  it('discarding removes both copies without marking it submitted', async () => {
    render(<Harness />);
    await act(async () => { screen.getByText('discard').click(); });
    await waitFor(() => expect(draftService.discardDraft)
      .toHaveBeenCalledWith('memo', 'new'));
  });

  it('an explicit save is retained as a milestone', async () => {
    render(<Harness />);
    await type('deliberate');
    await act(async () => { screen.getByText('save now').click(); });
    await waitFor(() => expect(draftService.saveDraft).toHaveBeenCalled());
    expect(draftService.saveDraft.mock.calls.at(-1)[3])
      .toMatchObject({ milestone: 'manual_save' });
  });
});
