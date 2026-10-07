import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { DraftMemos, DepartmentMemos, DraftForReview, ArchivedMemos } from './scopePages';

vi.mock('../../services/memoService', () => ({
  memoService: {
    list: vi.fn(() => Promise.resolve({ results: [], count: 0 })),
    departments: vi.fn(() => Promise.resolve([])),
    export: vi.fn(),
  },
}));

const renderPage = (Page) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter><Page /></MemoryRouter>
    </QueryClientProvider>,
  );
};

/**
 * These four menus all render through MemoScopePage. A crash in that one shared
 * component blanks every menu at once while the dashboard keeps working, which is
 * exactly how the type-filter regression reached a user — so each menu gets its
 * own mount here rather than a single MemoScopePage smoke test.
 */
describe('memo scope pages mount', () => {
  const pages = [
    ['Draft Memo', DraftMemos],
    ['Department Memo', DepartmentMemos],
    ['Draft For Review', DraftForReview],
    ['Archived Memo', ArchivedMemos],
  ];

  beforeEach(() => vi.clearAllMocks());

  it.each(pages)('%s renders its heading without throwing', async (title, Page) => {
    const errors = [];
    const spy = vi.spyOn(console, 'error').mockImplementation((...a) => errors.push(a.join(' ')));
    renderPage(Page);
    await waitFor(() => expect(screen.getByRole('heading', { name: title })).toBeInTheDocument());
    spy.mockRestore();
    expect(errors).toEqual([]);
  });

  /* EmptyState is shared with the leave module, where its default CTA is
     "Apply for leave" -> /leave/apply. A memo menu must never offer that. */
  it.each([
    ['Draft For Review', DraftForReview],
    ['Archived Memo', ArchivedMemos],
    ['Department Memo', DepartmentMemos],
  ])('%s offers no leave CTA when it is empty', async (title, Page) => {
    renderPage(Page);
    await screen.findByRole('heading', { name: title });
    expect(screen.queryByRole('button', { name: /apply for leave/i })).toBeNull();
  });

  it('Draft Memo offers Create Memo when it is empty', async () => {
    renderPage(DraftMemos);
    const cta = await screen.findByRole('button', { name: 'Create Memo' });
    expect(cta).toBeInTheDocument();
  });

  it('renders the memo type filter with the manual’s three types', async () => {
    renderPage(DraftMemos);
    const select = await screen.findByLabelText('Filter by type');
    const options = [...select.querySelectorAll('option')].map((o) => `${o.value}:${o.textContent}`);
    expect(options).toEqual([
      ':All types', 'general:GENERAL', 'confidential:CONFIDENTIAL', 'draft:DRAFT',
    ]);
  });
});
