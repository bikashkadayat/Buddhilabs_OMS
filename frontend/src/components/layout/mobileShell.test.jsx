import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import MobileTabBar from './MobileTabBar';
import CreateSheet from './CreateSheet';
import MobileQueueCards from '../queue/MobileQueueCards';

/**
 * Mobile shell behaviour (Phase F).
 *
 * Four things the brief names, plus the one it does not: that a card cannot
 * fire a rejection without a reason. The cards and the desktop rows share
 * useRowAction precisely so that rule cannot be lost in one of them.
 */

let role = 'maker';
vi.mock('../../hooks/useAuth', () => ({ useAuth: () => ({ role, user: { initials: 'BK' } }) }));

let queueCounts = { total: 0 };
vi.mock('../../hooks/useWorkQueue', () => ({
  useWorkQueue: () => ({ counts: queueCounts, allItems: [], act: vi.fn() }),
}));

const navigate = vi.fn();
vi.mock('react-router-dom', async (orig) => ({
  ...(await orig()),
  useNavigate: () => navigate,
}));

const wrap = (ui, entries = ['/']) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={entries}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  vi.clearAllMocks();
  role = 'maker';
  queueCounts = { total: 0 };
});

describe('bottom navigation', () => {
  it('is a labelled nav with exactly the five approved tabs, in order', () => {
    wrap(<MobileTabBar onCreate={vi.fn()} onSearch={vi.fn()} />);
    const nav = screen.getByRole('navigation', { name: 'Primary' });
    expect(nav).toBeInTheDocument();
    const labels = [...nav.querySelectorAll('.mtb-label')].map((n) => n.textContent);
    expect(labels).toEqual(['Home', 'Queue', 'Create', 'Search', 'Profile']);
  });

  it('keeps tab POSITION identical regardless of role', () => {
    // A tab that moves between users destroys the muscle memory a bottom bar
    // exists to create.
    const order = () => [...document.querySelectorAll('.mtb-label')].map((n) => n.textContent);
    const { unmount } = wrap(<MobileTabBar onCreate={vi.fn()} onSearch={vi.fn()} />);
    const asMaker = order();
    unmount();
    role = 'admin';
    wrap(<MobileTabBar onCreate={vi.fn()} onSearch={vi.fn()} />);
    expect(order()).toEqual(asMaker);
  });

  it('marks the current tab with aria-current', () => {
    wrap(<MobileTabBar onCreate={vi.fn()} onSearch={vi.fn()} />, ['/queue']);
    const queue = screen.getByRole('link', { name: /Queue/ });
    expect(queue).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: /Home/ })).not.toHaveAttribute('aria-current');
  });

  it('opens Create and Search as dialogs rather than navigating', () => {
    const onCreate = vi.fn();
    const onSearch = vi.fn();
    wrap(<MobileTabBar onCreate={onCreate} onSearch={onSearch} />);
    const create = screen.getByRole('button', { name: /Create/ });
    expect(create).toHaveAttribute('aria-haspopup', 'dialog');
    fireEvent.click(create);
    expect(onCreate).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /Search/ }));
    expect(onSearch).toHaveBeenCalled();
    // Neither may navigate - that is the whole point of an overlay.
    expect(navigate).not.toHaveBeenCalled();
  });

  it('announces the pending count to a screen reader, not just as a dot', () => {
    queueCounts = { total: 4 };
    wrap(<MobileTabBar onCreate={vi.fn()} onSearch={vi.fn()} />);
    // The accessible name concatenates the visible label and the sr-only
    // suffix, so whitespace between them is normalised but present.
    expect(screen.getByRole('link', { name: /Queue\s*,\s*4 waiting/ })).toBeInTheDocument();
  });

  it('caps the badge so a large number cannot break the layout', () => {
    queueCounts = { total: 43 };
    wrap(<MobileTabBar onCreate={vi.fn()} onSearch={vi.fn()} />);
    expect(screen.getByText('9+')).toBeInTheDocument();
  });

  it('shows no badge when nothing is waiting', () => {
    wrap(<MobileTabBar onCreate={vi.fn()} onSearch={vi.fn()} />);
    expect(document.querySelector('.mtb-dot')).toBeNull();
  });
});

describe('create sheet', () => {
  it('is a modal dialog and never navigates on open', () => {
    wrap(<CreateSheet open onClose={vi.fn()} />);
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(dialog).toHaveAccessibleName('Create');
    expect(navigate).not.toHaveBeenCalled();
  });

  it('renders nothing when closed', () => {
    const { container } = wrap(<CreateSheet open={false} onClose={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('offers the five approved creators to an Employee', () => {
    wrap(<CreateSheet open onClose={vi.fn()} />);
    for (const label of ['Create memo', 'Create minute', 'Create circular',
      'Apply for leave', 'Request an asset']) {
      expect(screen.getByRole('button', { name: new RegExp(label) })).toBeInTheDocument();
    }
  });

  it('hides self-service creators from Admin, matching roles.js', () => {
    role = 'admin';
    wrap(<CreateSheet open onClose={vi.fn()} />);
    expect(screen.queryByRole('button', { name: /Create memo/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Apply for leave/ })).toBeNull();
    // Ungated creators remain.
    expect(screen.getByRole('button', { name: /Create minute/ })).toBeInTheDocument();
  });

  it('closes before navigating, so the sheet never lingers over the new page', () => {
    const onClose = vi.fn();
    wrap(<CreateSheet open onClose={onClose} />);
    fireEvent.click(screen.getByRole('button', { name: /Create minute/ }));
    expect(onClose).toHaveBeenCalled();
    expect(navigate).toHaveBeenCalledWith('/minutes/create');
  });

  it('closes on Escape and on a backdrop press', () => {
    const onClose = vi.fn();
    const { container } = wrap(<CreateSheet open onClose={onClose} />);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
    onClose.mockClear();
    fireEvent.mouseDown(container.querySelector('.cs-backdrop'));
    expect(onClose).toHaveBeenCalled();
  });

  it('moves focus into the sheet', async () => {
    wrap(<CreateSheet open onClose={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByRole('dialog').contains(document.activeElement)).toBe(true);
    });
  });
});

describe('queue cards', () => {
  const item = (over = {}) => ({
    id: 'memo:1', type: 'memo', kind: 'approval',
    title: 'HR Budget Approval', subtitle: 'MEMO-1 · Finance',
    requester: 'Rita Sharma', requesterInitials: 'RS',
    createdAt: new Date(Date.now() - 3600_000).toISOString(), dueAt: null,
    href: '/memos/1',
    actions: [
      { label: 'Approve', verb: 'approve', variant: 'primary', run: vi.fn() },
      { label: 'Reject', verb: 'reject', variant: 'secondary', needsRemark: true, run: vi.fn() },
    ],
    ...over,
  });

  it('shows type, title, requester, waiting time and the actions', () => {
    wrap(<MobileQueueCards items={[item()]} onAction={vi.fn()} />);
    expect(screen.getByText('MEMO')).toBeInTheDocument();
    expect(screen.getByText('HR Budget Approval')).toBeInTheDocument();
    expect(screen.getByText(/Rita Sharma/)).toBeInTheDocument();
    expect(screen.getByText(/hour/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument();
  });

  it('runs a plain action immediately', async () => {
    const onAction = vi.fn().mockResolvedValue({ ok: true });
    wrap(<MobileQueueCards items={[item()]} onAction={onAction} />);
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(onAction).toHaveBeenCalled());
    expect(onAction.mock.calls[0][1].verb).toBe('approve');
  });

  it('will not reject without a reason', async () => {
    // The rule shared with the desktop row. A card that could fire a rejection
    // with an empty remark would put an unexplained decision in the audit trail.
    const onAction = vi.fn().mockResolvedValue({ ok: true });
    wrap(<MobileQueueCards items={[item()]} onAction={onAction} />);
    fireEvent.click(screen.getByRole('button', { name: 'Reject' }));
    // The reason is now asked for in a dialog (Phase MEMO-ACT-ENDPOINT-400-ROOT-
    // CAUSE), so the card's own Reject button is still there underneath - the
    // send button is the one inside the dialog.
    const dialog = within(await screen.findByRole('dialog'));
    expect(dialog.getByRole('button', { name: 'Reject' })).toBeDisabled();
    expect(onAction).not.toHaveBeenCalled();

    fireEvent.change(dialog.getByLabelText(/Remarks/), {
      target: { value: 'Budget line is wrong' },
    });
    await waitFor(() => expect(dialog.getByRole('button', { name: 'Reject' })).toBeEnabled());
    fireEvent.click(dialog.getByRole('button', { name: 'Reject' }));
    await waitFor(() => expect(onAction).toHaveBeenCalled());
    expect(onAction.mock.calls[0][2]).toBe('Budget line is wrong');
  });

  it('offers Open instead of a decision when the item has no actions', () => {
    wrap(<MobileQueueCards items={[item({ actions: [] })]} onAction={vi.fn()} />);
    expect(screen.getByRole('link', { name: 'Open' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
  });

  it('shows a resolved item as done rather than offering the action again', () => {
    wrap(<MobileQueueCards items={[item({ resolved: { verb: 'approve' } })]} onAction={vi.fn()} />);
    expect(screen.getByText(/Approved/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
  });

  it('marks an overdue card so urgency survives greyscale', () => {
    const overdue = item({ dueAt: new Date(Date.now() - 86_400_000).toISOString() });
    const { container } = wrap(<MobileQueueCards items={[overdue]} onAction={vi.fn()} />);
    expect(container.querySelector('.mqc.is-overdue')).toBeInTheDocument();
    expect(screen.getByText(/overdue/)).toBeInTheDocument();
  });
});
