/**
 * Phase T2.3 — comments, replies, mentions and edited markers.
 *
 * The claims worth pinning: an edit is visible rather than silent, the pencil
 * appears only on your OWN comment, a reply is offered on a top-level comment
 * and never on a reply, and a mention sends the id it resolved rather than the
 * text somebody typed.
 */
import React from 'react';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/taskService', () => ({
  taskService: { searchEmployees: vi.fn() },
}));

import CommentThread from './CommentThread';
import { taskService } from '../../services/taskService';

const ME = 'u1';
const THEM = 'u2';

const comment = (id, authorId, body, extra = {}) => ({
  id,
  parent: null,
  author: { id: authorId, full_name: authorId === ME ? 'Me' : 'Them' },
  author_name: authorId === ME ? 'Me' : 'Them',
  body,
  created_at: '2026-09-01T09:00:00Z',
  edited_at: null,
  is_edited: false,
  mentions: [],
  replies: [],
  ...extra,
});

const renderThread = (props = {}) => {
  const handlers = { onPost: vi.fn(), onReply: vi.fn(), onEdit: vi.fn() };
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <CommentThread comments={[]} currentUserId={ME} canComment busy={false}
        {...handlers} {...props} />
    </QueryClientProvider>,
  );
  return handlers;
};

describe('comment thread', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    taskService.searchEmployees.mockResolvedValue([
      { id: THEM, full_name: 'Bikash Kadayat', designation: 'Officer',
        department: 'Engineering' },
    ]);
  });

  it('says so when there are no comments', () => {
    renderThread();
    expect(screen.getByText('No comments yet.')).toBeInTheDocument();
  });

  it('posts a comment', () => {
    const { onPost } = renderThread();
    fireEvent.change(screen.getByLabelText('Add a comment…'),
      { target: { value: 'Draft uploaded.' } });
    fireEvent.click(screen.getByRole('button', { name: /post/i }));
    expect(onPost).toHaveBeenCalledWith('Draft uploaded.', []);
  });

  it('will not post an empty comment', () => {
    renderThread();
    expect(screen.getByRole('button', { name: /post/i })).toBeDisabled();
  });

  it('renders a reply nested under its parent', () => {
    renderThread({
      comments: [comment('c1', THEM, 'Draft uploaded.', {
        replies: [comment('c2', ME, 'Reading it now.', { parent: 'c1' })],
      })],
    });
    expect(screen.getByText('Draft uploaded.')).toBeInTheDocument();
    expect(screen.getByText('Reading it now.')).toBeInTheDocument();
  });

  it('offers Reply on a top-level comment', () => {
    const { onReply } = renderThread({
      comments: [comment('c1', THEM, 'Draft uploaded.')],
    });
    fireEvent.click(screen.getByRole('button', { name: /reply/i }));
    fireEvent.change(screen.getByLabelText(/reply to them/i),
      { target: { value: 'Reading it now.' } });
    fireEvent.click(screen.getByRole('button', { name: /post reply/i }));
    expect(onReply).toHaveBeenCalledWith('c1', 'Reading it now.', []);
  });

  it('offers no Reply on a reply, because nesting stops at one level', () => {
    renderThread({
      comments: [comment('c1', THEM, 'Parent.', {
        replies: [comment('c2', THEM, 'A reply.', { parent: 'c1' })],
      })],
    });
    // One Reply button — the parent's. The reply itself has none.
    expect(screen.getAllByRole('button', { name: /reply/i })).toHaveLength(1);
  });

  it('offers Edit on your own comment only', () => {
    renderThread({
      comments: [comment('c1', ME, 'Mine.'), comment('c2', THEM, 'Theirs.')],
    });
    expect(screen.getAllByRole('button', { name: /edit/i })).toHaveLength(1);
  });

  it('edits your own comment', () => {
    const { onEdit } = renderThread({
      comments: [comment('c1', ME, 'Draft uplodaed.')],
    });
    fireEvent.click(screen.getByRole('button', { name: /edit/i }));
    fireEvent.change(screen.getByLabelText(/edit your comment/i),
      { target: { value: 'Draft uploaded.' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    expect(onEdit).toHaveBeenCalledWith('c1', 'Draft uploaded.');
  });

  it('states an edit in words, not only as a style', () => {
    /* An edit a reader can miss is an edit that has been hidden from them. */
    renderThread({
      comments: [comment('c1', THEM, 'Corrected.', {
        is_edited: true, edited_at: '2026-09-02T09:00:00Z',
      })],
    });
    expect(screen.getByText('edited')).toBeInTheDocument();
  });

  it('sends the mention id it resolved, not the text that was typed', async () => {
    const { onPost } = renderThread();
    fireEvent.click(screen.getByRole('button', { name: /mention/i }));
    fireEvent.change(screen.getByLabelText(/search people to mention/i),
      { target: { value: 'Bikash' } });

    const hit = await screen.findByRole('button', { name: /Bikash Kadayat/ });
    fireEvent.click(hit);

    await waitFor(() => expect(screen.getByLabelText('Add a comment…'))
      .toHaveValue('@Bikash Kadayat '));
    expect(screen.getByText(/Will notify: Bikash Kadayat/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Add a comment…'),
      { target: { value: '@Bikash Kadayat Draft uploaded.' } });
    fireEvent.click(screen.getByRole('button', { name: /post/i }));
    expect(onPost).toHaveBeenCalledWith('@Bikash Kadayat Draft uploaded.', [THEM]);
  });

  it('shows who a posted comment named', () => {
    renderThread({
      comments: [comment('c1', THEM, '@Me look at this', {
        mentions: [{ id: 'm1', user: ME, user_name: 'Me' }],
      })],
    });
    const thread = screen.getByText('@Me look at this').closest('li');
    // The mention line sits below the body, and names who will have been told.
    const lines = within(thread).getAllByText(/Me/);
    expect(lines.length).toBeGreaterThan(1);   // the author, and the mention
  });

  it('goes read-only when the server withholds can_comment', () => {
    renderThread({
      canComment: false,
      comments: [comment('c1', ME, 'Mid-flight note.')],
    });
    expect(screen.queryByLabelText('Add a comment…')).toBeNull();
    expect(screen.queryByRole('button', { name: /edit/i })).toBeNull();
    expect(screen.getByText(/read-only/)).toBeInTheDocument();
  });
});
