import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import CommandPalette from './CommandPalette';

/**
 * Command palette behaviour (Phase 204).
 *
 * Covers the three things the brief names and one it does not: that the palette
 * cannot offer an action the server would refuse. Remote sources are stubbed at
 * the axios layer so these are real component tests, not mocks of my own hook.
 */

const navigate = vi.fn();
vi.mock('react-router-dom', async (orig) => ({
  ...(await orig()),
  useNavigate: () => navigate,
}));

vi.mock('../../hooks/useAuth', () => ({ useAuth: () => ({ role: 'checker' }) }));

const act = vi.fn().mockResolvedValue({ ok: true });
let queueItems = [];
vi.mock('../../hooks/useWorkQueue', () => ({
  useWorkQueue: () => ({ allItems: queueItems, act }),
}));

const get = vi.fn();
vi.mock('../../services/api', () => ({ default: { get: (...a) => get(...a) } }));

const renderPalette = (props = {}) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <CommandPalette open onClose={props.onClose || vi.fn()} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

const type = (value) => fireEvent.change(screen.getByRole('combobox'), { target: { value } });

beforeEach(() => {
  vi.clearAllMocks();
  queueItems = [];
  get.mockResolvedValue({ data: { results: [] } });
});

describe('combobox accessibility', () => {
  it('is a modal dialog with an accessible name', () => {
    renderPalette();
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(dialog).toHaveAccessibleName('Search the workspace');
  });

  it('declares the full combobox contract', () => {
    renderPalette();
    const input = screen.getByRole('combobox');
    expect(input).toHaveAttribute('aria-autocomplete', 'list');
    expect(input).toHaveAttribute('aria-controls');
    expect(input).toHaveAccessibleName();
  });

  it('takes focus on open', async () => {
    renderPalette();
    await waitFor(() => expect(screen.getByRole('combobox')).toHaveFocus());
  });

  it('points aria-activedescendant at the highlighted option, and the input keeps focus', async () => {
    renderPalette();
    type('queue');
    const input = screen.getByRole('combobox');
    await waitFor(() => expect(input).toHaveAttribute('aria-activedescendant'));
    const id = input.getAttribute('aria-activedescendant');
    expect(document.getElementById(id)).toHaveAttribute('aria-selected', 'true');
    // The caret must never leave the field, or typing would break.
    expect(input).toHaveFocus();
  });

  it('announces the result count politely', async () => {
    renderPalette();
    type('queue');
    await waitFor(() => {
      const live = document.querySelector('[aria-live="polite"]');
      expect(live).toHaveTextContent(/result/i);
    });
  });

  it('exposes one listbox with labelled groups inside it', async () => {
    renderPalette();
    type('memo');
    await waitFor(() => expect(screen.getAllByRole('listbox')).toHaveLength(1));
    expect(screen.getAllByRole('group').length).toBeGreaterThan(0);
  });
});

describe('keyboard navigation', () => {
  const press = (key) => fireEvent.keyDown(screen.getByRole('combobox'), { key });
  const activeText = () => {
    const id = screen.getByRole('combobox').getAttribute('aria-activedescendant');
    return document.getElementById(id)?.textContent || '';
  };

  it('starts on the first option', async () => {
    renderPalette();
    type('memo');
    await waitFor(() => expect(screen.getAllByRole('option').length).toBeGreaterThan(1));
    const first = screen.getAllByRole('option')[0];
    expect(first).toHaveAttribute('aria-selected', 'true');
  });

  it('moves down and up with the arrow keys', async () => {
    renderPalette();
    type('memo');
    await waitFor(() => expect(screen.getAllByRole('option').length).toBeGreaterThan(1));
    const firstText = activeText();
    press('ArrowDown');
    await waitFor(() => expect(activeText()).not.toBe(firstText));
    press('ArrowUp');
    await waitFor(() => expect(activeText()).toBe(firstText));
  });

  it('wraps around at both ends rather than dead-ending', async () => {
    renderPalette();
    type('memo');
    await waitFor(() => expect(screen.getAllByRole('option').length).toBeGreaterThan(1));
    const count = screen.getAllByRole('option').length;
    const firstText = activeText();
    for (let i = 0; i < count; i += 1) press('ArrowDown');
    await waitFor(() => expect(activeText()).toBe(firstText));
    press('ArrowUp');
    await waitFor(() => expect(activeText()).not.toBe(firstText));
  });

  it('jumps to the ends with Home and End', async () => {
    renderPalette();
    type('memo');
    await waitFor(() => expect(screen.getAllByRole('option').length).toBeGreaterThan(1));
    const options = screen.getAllByRole('option');
    press('End');
    await waitFor(() => expect(options[options.length - 1]).toHaveAttribute('aria-selected', 'true'));
    press('Home');
    await waitFor(() => expect(screen.getAllByRole('option')[0]).toHaveAttribute('aria-selected', 'true'));
  });

  it('Enter runs the highlighted option', async () => {
    renderPalette();
    type('work queue');
    await waitFor(() => expect(screen.getAllByRole('option').length).toBeGreaterThan(0));
    press('Enter');
    await waitFor(() => expect(navigate).toHaveBeenCalled());
  });

  it('Escape closes without navigating', async () => {
    const onClose = vi.fn();
    renderPalette({ onClose });
    press('Escape');
    expect(onClose).toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();
  });

  it('Enter on an empty result set does nothing', () => {
    renderPalette();
    press('Enter');
    expect(navigate).not.toHaveBeenCalled();
  });
});

describe('results and actions', () => {
  it('groups local hits under Navigation and Quick actions', async () => {
    renderPalette();
    type('memo');
    await waitFor(() => expect(screen.getByRole('listbox')).toBeInTheDocument());
    const names = screen.getAllByRole('group').map((g) => g.getAttribute('aria-label'));
    expect(names).toContain('Navigation');
  });

  it('shows remote hits in their own group', async () => {
    get.mockImplementation((url) => Promise.resolve({
      data: url === '/memos/'
        ? { results: [{ id: 9, subject: 'Budget approval', memo_number: 'M-9' }] }
        : { results: [] },
    }));
    renderPalette();
    type('budget');
    await waitFor(() => {
      expect(screen.getByText('Budget approval')).toBeInTheDocument();
    }, { timeout: 3000 });
    const memoGroup = screen.getByRole('group', { name: 'Memos' });
    expect(within(memoGroup).getByText('Budget approval')).toBeInTheDocument();
  });

  it('offers Approve only when the work queue says the record is actionable', async () => {
    get.mockImplementation((url) => Promise.resolve({
      data: url === '/memos/'
        ? { results: [{ id: 9, subject: 'Budget approval' }] }
        : { results: [] },
    }));
    queueItems = [{
      id: 'memo:9',
      actions: [{ label: 'Approve', verb: 'approve', run: vi.fn() }],
    }];
    renderPalette();
    type('budget');
    const approve = await screen.findByRole('button', { name: /Approve: Budget approval/ }, { timeout: 3000 });
    fireEvent.click(approve);
    await waitFor(() => expect(act).toHaveBeenCalled());
    // It must call the queue's action, which is the same service method the
    // module page uses - search never posts its own decision.
    expect(act.mock.calls[0][1].verb).toBe('approve');
  });

  it('offers no Approve when the record is not in the queue', async () => {
    get.mockImplementation((url) => Promise.resolve({
      data: url === '/memos/'
        ? { results: [{ id: 9, subject: 'Budget approval' }] }
        : { results: [] },
    }));
    queueItems = [];
    renderPalette();
    type('budget');
    await screen.findByText('Budget approval', {}, { timeout: 3000 });
    expect(screen.queryByRole('button', { name: /Approve/ })).not.toBeInTheDocument();
  });

  it('asks for two characters before searching', async () => {
    renderPalette();
    type('b');
    await waitFor(() => expect(screen.getByText(/at least two characters/i)).toBeInTheDocument());
    expect(get).not.toHaveBeenCalled();
  });

  it('says so when a source could not be searched, and still shows the rest', async () => {
    get.mockImplementation((url) => (url === '/minutes/'
      ? Promise.reject(new Error('boom'))
      : Promise.resolve({ data: { results: [] } })));
    renderPalette();
    type('budget');
    await waitFor(() => {
      expect(screen.getByText(/could not be searched/i)).toBeInTheDocument();
    }, { timeout: 3000 });
  });
});

describe('mount/unmount stability', () => {
  /**
   * A hook-order warning appeared once in the dev log while this file was being
   * edited under HMR. Every hook sits before the `if (!open) return null`, so it
   * was a Fast Refresh artifact rather than a defect - but "I read the code and
   * it looks fine" is not evidence, and toggling open is exactly the sequence
   * that would expose a real one.
   */
  it('survives being toggled closed and open repeatedly', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = (open) => (
      <QueryClientProvider client={qc}>
        <MemoryRouter><CommandPalette open={open} onClose={vi.fn()} /></MemoryRouter>
      </QueryClientProvider>
    );
    const errors = [];
    const spy = vi.spyOn(console, 'error').mockImplementation((...a) => errors.push(a.join(' ')));

    const { rerender } = render(view(false));
    for (let i = 0; i < 3; i += 1) {
      rerender(view(true));
      await waitFor(() => expect(screen.getByRole('combobox')).toBeInTheDocument());
      rerender(view(false));
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    }
    expect(errors.filter((e) => /order of Hooks|Should have a queue/.test(e))).toEqual([]);
    spy.mockRestore();
  });

  it('clears the query when it closes, so a shared machine leaks nothing', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = (open) => (
      <QueryClientProvider client={qc}>
        <MemoryRouter><CommandPalette open={open} onClose={vi.fn()} /></MemoryRouter>
      </QueryClientProvider>
    );
    const { rerender } = render(view(true));
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'salary review' } });
    rerender(view(false));
    rerender(view(true));
    await waitFor(() => expect(screen.getByRole('combobox')).toHaveValue(''));
  });
});
