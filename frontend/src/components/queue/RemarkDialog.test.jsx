import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import QueueRow from './QueueRow';
import MobileQueueCards from './MobileQueueCards';
import { remarkLength } from './remarkRules';

const FLOW = {
  roleLabel: 'Reviewer', position: 1, total: 4,
  next: { roleLabel: 'Recommender', name: 'Sanjaya Poudel' },
  isFinalStep: false, authorName: 'Prashanta Acharya',
};
const action = (over = {}) => ({
  label: 'Review', verb: 'review', variant: 'primary', needsRemark: true, minRemark: 10,
  dialogTitle: 'Review memo', workflow: FLOW, run: vi.fn(), ...over,
});
const item = (over = {}) => ({
  id: 'memo:1', type: 'memo', kind: 'review', title: 'HR Notice',
  requester: 'Prashanta Acharya', requesterInitials: 'PA',
  createdAt: new Date().toISOString(), href: '/memos/1',
  stepLine: 'Reviewer · Step 1 of 4 · Next: Recommender',
  actions: [
    action(),
    action({ label: 'Reject', verb: 'reject', variant: 'secondary', dialogTitle: 'Reject memo' }),
  ],
  ...over,
});
const mount = (props) => render(<MemoryRouter><QueueRow {...props} /></MemoryRouter>);
const openDialog = async (name = 'Review') => {
  fireEvent.click(screen.getByRole('button', { name }));
  return within(await screen.findByRole('dialog'));
};
const type = (dialog, value) => fireEvent.change(dialog.getByLabelText(/Remarks/), { target: { value } });
const send = (dialog, name = 'Review') => dialog.getByRole('button', { name });

describe('remark length is counted as the server counts it', () => {
  it('counts code points, so an emoji is one character, as Python len() says', () => {
    expect('👍👍👍👍👍'.length).toBe(10);          // what the dialog used to count
    expect(remarkLength('👍👍👍👍👍')).toBe(5);    // what the server counts
    expect(remarkLength('  padded  ')).toBe(6);    // stripped first, as the server does
    expect(remarkLength('ठीक छ, अगाडि बढाउनुहोस्')).toBe([...'ठीक छ, अगाडि बढाउनुहोस्'].length);
  });

  it('keeps the send button disabled for ten emoji-units that are only five characters', async () => {
    mount({ item: item(), onAction: vi.fn() });
    const dialog = await openDialog();
    type(dialog, '👍👍👍👍👍');
    expect(send(dialog)).toBeDisabled();
    expect(dialog.getByText('5 / 10 minimum')).toBeInTheDocument();
  });
});

describe('the remark dialog', () => {
  it('is used by every memo role, with the field labelled "Remarks *"', async () => {
    for (const [label, verb] of [['Review', 'review'], ['Recommend', 'recommend'],
      ['Support', 'support'], ['Approve', 'approve']]) {
      const onAction = vi.fn();
      const { unmount } = mount({ item: item({ actions: [action({ label, verb, dialogTitle: `${label} memo` })] }), onAction });
      const dialog = await openDialog(label);
      expect(screen.getByRole('dialog', { name: `${label} memo` })).toBeInTheDocument();
      const field = dialog.getByLabelText(/Remarks/);
      expect(field).toBeRequired();
      expect(dialog.getByText('*')).toBeInTheDocument();
      expect(onAction).not.toHaveBeenCalled();
      unmount();
    }
  });

  it('shows the role, the current step and where the memo goes next', async () => {
    mount({ item: item(), onAction: vi.fn() });
    const flow = within((await openDialog()).getByTestId('remark-dialog-flow'));
    expect(flow.getByText('Reviewer')).toBeInTheDocument();
    expect(flow.getByText('Step 1 of 4')).toBeInTheDocument();
    expect(flow.getByText('Moves to Recommender — Sanjaya Poudel.')).toBeInTheDocument();
  });

  it('says a final approval files the memo, and a rejection returns it to the author', async () => {
    const final = { ...FLOW, roleLabel: 'Approver', position: 4, next: null, isFinalStep: true };
    const { unmount } = mount({ item: item({ actions: [action({ label: 'Approve', workflow: final })] }), onAction: vi.fn() });
    expect((await openDialog('Approve')).getByText('The memo is approved and filed.')).toBeInTheDocument();
    unmount();

    mount({ item: item(), onAction: vi.fn() });
    const reject = await openDialog('Reject');
    expect(reject.getByText('If rejected')).toBeInTheDocument();
    expect(reject.getByText('Returned to Prashanta Acharya to revise.')).toBeInTheDocument();
  });

  it('shows the step on the queue item itself, before any button is pressed', () => {
    mount({ item: item(), onAction: vi.fn() });
    expect(screen.getByTestId('queue-step')).toHaveTextContent('Reviewer · Step 1 of 4 · Next: Recommender');
  });

  it('counts, explains and only enables send once the remark is valid', async () => {
    const onAction = vi.fn().mockResolvedValue({ ok: true });
    mount({ item: item(), onAction });
    const dialog = await openDialog();

    expect(dialog.getByText('0 / 10 minimum')).toBeInTheDocument();
    expect(send(dialog)).toBeDisabled();

    type(dialog, 'ok');
    expect(dialog.getByText('2 / 10 minimum')).toBeInTheDocument();
    expect(dialog.getByText('At least 10 characters are required — 8 more to go.')).toBeInTheDocument();
    expect(send(dialog)).toBeDisabled();

    type(dialog, '');
    fireEvent.blur(dialog.getByLabelText(/Remarks/));
    expect(dialog.getByText('Remarks are required.')).toBeInTheDocument();

    type(dialog, 'Go ahead 11');
    expect(dialog.getByText('11 / 10 minimum')).toBeInTheDocument();
    expect(dialog.getByText('Looks good.')).toBeInTheDocument();
    expect(send(dialog)).toBeEnabled();

    fireEvent.click(send(dialog));
    await waitFor(() => expect(onAction).toHaveBeenCalled());
    expect(onAction.mock.calls[0][2]).toBe('Go ahead 11');
  });

  it('never sends an invalid remark, even if the form is submitted another way', async () => {
    const onAction = vi.fn();
    mount({ item: item(), onAction });
    const dialog = await openDialog();
    type(dialog, 'too short');
    fireEvent.submit(dialog.getByTestId('remark-dialog'));
    expect(onAction).not.toHaveBeenCalled();
  });

  it('stays open with the server\'s message and the typed remark if the send is still refused', async () => {
    const onAction = vi.fn().mockResolvedValue({ ok: false, error: 'This memo is cancelled and can no longer be actioned.' });
    mount({ item: item(), onAction });
    const dialog = await openDialog();
    type(dialog, 'Looks fine to me');
    fireEvent.click(send(dialog));
    expect(await dialog.findByText('This memo is cancelled and can no longer be actioned.')).toBeInTheDocument();
    expect(dialog.getByLabelText(/Remarks/)).toHaveValue('Looks fine to me');
    expect(screen.queryByText(/status code/)).toBeNull();
  });
});

describe('checking the item is still actionable when the dialog opens', () => {
  it('says so BEFORE anything is typed when the memo has moved on, and cannot be sent', async () => {
    const precheck = vi.fn().mockResolvedValue({
      ok: false, reason: 'This memo has moved on — it is now with Sanjaya Poudel (Recommender).' });
    const onAction = vi.fn();
    mount({ item: item({ actions: [action({ precheck })] }), onAction });
    const dialog = await openDialog();

    expect(await dialog.findByTestId('remark-dialog-blocked'))
      .toHaveTextContent('This memo has moved on — it is now with Sanjaya Poudel (Recommender).');
    expect(dialog.queryByLabelText(/Remarks/)).toBeNull();
    expect(dialog.queryByRole('button', { name: 'Review' })).toBeNull();
    expect(onAction).not.toHaveBeenCalled();
  });

  it('holds the field while checking, then allows typing once confirmed', async () => {
    let release;
    const precheck = vi.fn(() => new Promise((r) => { release = r; }));
    mount({ item: item({ actions: [action({ precheck })] }), onAction: vi.fn() });
    const dialog = await openDialog();

    expect(dialog.getByText(/Confirming this is still waiting on you/)).toBeInTheDocument();
    expect(dialog.getByLabelText(/Remarks/)).toBeDisabled();
    expect(dialog.getByRole('button', { name: 'Checking…' })).toBeDisabled();

    await act(async () => release({ ok: true }));
    expect(dialog.getByLabelText(/Remarks/)).toBeEnabled();
  });

  it('does not lock the person out when the check itself fails to load', async () => {
    const precheck = vi.fn().mockRejectedValue(new Error('network'));
    mount({ item: item({ actions: [action({ precheck })] }), onAction: vi.fn() });
    const dialog = await openDialog();
    await waitFor(() => expect(dialog.getByLabelText(/Remarks/)).toBeEnabled());
  });

  it('behaves identically on the mobile card', async () => {
    const precheck = vi.fn().mockResolvedValue({ ok: false, reason: 'This memo is no longer waiting on you.' });
    render(<MemoryRouter><MobileQueueCards items={[item({ actions: [action({ precheck })] })]} onAction={vi.fn()} /></MemoryRouter>);
    const dialog = await openDialog();
    expect(await dialog.findByTestId('remark-dialog-blocked')).toHaveTextContent('no longer waiting on you');
    expect(screen.getByTestId('queue-step')).toHaveTextContent('Step 1 of 4');
  });
});


describe('a blocked dialog does not describe a future that will not happen', () => {
  it('hides the next-step context and refreshes the queue when closed', async () => {
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    const precheck = vi.fn().mockResolvedValue({ ok: false, reason: 'This memo is cancelled and can no longer be actioned.' });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter><QueueRow item={item({ actions: [action({ precheck })] })} onAction={vi.fn()} /></MemoryRouter>
      </QueryClientProvider>,
    );
    const dialog = await openDialog();
    await dialog.findByTestId('remark-dialog-blocked');
    expect(dialog.queryByTestId('remark-dialog-flow')).toBeNull();
    expect(dialog.queryByText(/Moves to Recommender/)).toBeNull();

    fireEvent.click(dialog.getByRole('button', { name: 'Back to queue' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['workqueue'] });
  });

  it('does not refresh the queue when an ordinary dialog is cancelled', async () => {
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter><QueueRow item={item()} onAction={vi.fn()} /></MemoryRouter>
      </QueryClientProvider>,
    );
    const dialog = await openDialog();
    fireEvent.click(dialog.getByRole('button', { name: 'Cancel' }));
    expect(invalidate).not.toHaveBeenCalled();
  });
});
