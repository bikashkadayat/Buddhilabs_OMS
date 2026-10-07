import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const EMPLOYEES = [
  { id: 'e1', full_name: 'Asha Rai', designation: 'Senior Officer', department: 'Finance', role: 'maker', role_display: 'Employee' },
  { id: 'e2', full_name: 'Bikash Thapa', designation: 'Director', department: 'Finance', role: 'approver', role_display: 'HR' },
];

vi.mock('../../services/memoService', () => ({
  memoService: {
    listTemplates: vi.fn(() => Promise.resolve([
      { id: 't1', name: 'General Announcement', memo_type: 'general', subject_template: 'Announcement: Topic', body_template: '<p>Body</p>' },
    ])),
    createMemo: vi.fn(() => Promise.resolve({ id: 'm1', memo_number: 'NIFN-GEN-2026-0001' })),
    createAndSubmit: vi.fn(() => Promise.resolve({ id: 'm1', memo_number: 'NIFN-GEN-2026-0001', status: 'draft_for_review' })),
    setMatrix: vi.fn(() => Promise.resolve({})),
    updateMemo: vi.fn(() => Promise.resolve({ id: 'm1', memo_number: 'NIFN-GEN-2026-0001' })),
    uploadAttachments: vi.fn(() => Promise.resolve([])),
    // Both were missing from this mock, so the page called `undefined()` the
    // moment the flow was reordered. They are part of the submit path now:
    // sections and matrix are written while the memo is still a draft, and
    // sendForReview is what locks it.
    setSections: vi.fn(() => Promise.resolve([])),
    sendForReview: vi.fn(() => Promise.resolve({ id: 'm1', status: 'draft_for_review' })),
    searchEmployees: vi.fn(() => Promise.resolve(EMPLOYEES)),
  },
}));
// Keep TipTap out of jsdom; test the form logic.
vi.mock('../../components/memo/RichTextEditor', () => ({ default: ({ value, onChange }) => (
  <textarea aria-label="Body" value={value} onChange={(e) => onChange?.(e.target.value)} />) }));
vi.mock('../../hooks/useAuth', () => ({ useAuth: () => ({ user: { id: 'u1' }, role: 'checker' }) }));

import { memoService } from '../../services/memoService';
import CreateMemo from './CreateMemo';

const renderPage = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><MemoryRouter><CreateMemo /></MemoryRouter></QueryClientProvider>,
  );
};

const fillDetails = () => {
  fireEvent.change(screen.getByLabelText('To'), { target: { value: 'My memo' } });
  fireEvent.change(screen.getByLabelText('Subject'), { target: { value: 'Subj' } });
};

/** Search for, and add, an employee to the approval matrix. */
const addEmployee = async (name) => {
  fireEvent.change(screen.getByLabelText('Search employee'), { target: { value: 'th' } });
  const option = await screen.findByRole('option', { name: new RegExp(name) });
  fireEvent.click(option);
};

describe('CreateMemo', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders the form for a non-maker role (checker)', () => {
    renderPage();
    expect(screen.getByRole('heading', { name: 'Create Memo' })).toBeInTheDocument();
    expect(screen.getByLabelText('To')).toBeInTheDocument();
  });

  it('loads a template into the form', async () => {
    renderPage();
    await screen.findByRole('option', { name: 'General Announcement' });
    fireEvent.change(screen.getByLabelText('Load template'), { target: { value: 't1' } });
    await waitFor(() => expect(screen.getByLabelText('Subject')).toHaveValue('Announcement: Topic'));
  });

  it('Save as Draft needs only the memo details, not a workflow', async () => {
    renderPage();
    fillDetails();
    fireEvent.click(screen.getByRole('button', { name: /Save as draft/ }));
    await waitFor(() => expect(memoService.createMemo).toHaveBeenCalled());
  });

  it('Send for Review stays disabled until the details AND a valid workflow exist', async () => {
    renderPage();
    const send = screen.getByRole('button', { name: /Submit for Approval/ });
    expect(send).toBeDisabled();

    fillDetails();
    // Details alone are not enough: without an approval workflow there is nobody
    // to route the memo to, and the server would reject the submission.
    expect(send).toBeDisabled();

    await addEmployee('Bikash Thapa');
    // Rows default to Reviewer, so the chain does not yet end in an Approver.
    expect(send).toBeDisabled();
    expect(screen.getByText(/final step must be an Approver/i)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Role type for Bikash Thapa'), { target: { value: 'approver' } });
    expect(send).toBeEnabled();
  });

  it('fills the memo BEFORE submitting it, in that order', async () => {
    /*
     * This asserted the old flow: `createAndSubmit` first, children after.
     * That order is what broke the module — submitting LOCKS the memo, so the
     * sections written next came back 403 (twelve times in one session's server
     * log) and the attachments after them never ran. The author saw a success
     * dialog and a memo with no body and no files.
     *
     * The order is the fix, so the order is what this test now pins.
     */
    renderPage();
    fillDetails();
    await addEmployee('Asha Rai');
    await addEmployee('Bikash Thapa');
    fireEvent.change(screen.getByLabelText('Role type for Bikash Thapa'), { target: { value: 'approver' } });

    fireEvent.click(screen.getByRole('button', { name: /Submit for Approval/ }));
    await waitFor(() => expect(memoService.sendForReview).toHaveBeenCalled());

    // A draft is created first, never create-and-submit.
    expect(memoService.createAndSubmit).not.toHaveBeenCalled();
    expect(memoService.createMemo).toHaveBeenCalled();

    // The matrix still carries the roles in the order they were added.
    expect(memoService.setMatrix.mock.calls[0][1]).toEqual([
      { assignee_id: 'e1', role_type: 'reviewer' },
      { assignee_id: 'e2', role_type: 'approver' },
    ]);

    // And the lock comes last: sections and matrix are written before submit.
    const order = [
      memoService.setSections.mock.invocationCallOrder[0],
      memoService.setMatrix.mock.invocationCallOrder[0],
      memoService.sendForReview.mock.invocationCallOrder[0],
    ];
    expect(order).toEqual([...order].sort((a, b) => a - b));
  });

  it('shows a workflow error returned by the server against the matrix', async () => {
    // The workflow is rejected at setMatrix now, not at create-and-submit.
    memoService.setMatrix.mockRejectedValueOnce({
      response: { data: { workflow: ['Asha Rai appears more than once.'] } },
    });
    renderPage();
    fillDetails();
    await addEmployee('Bikash Thapa');
    fireEvent.change(screen.getByLabelText('Role type for Bikash Thapa'), { target: { value: 'approver' } });
    fireEvent.click(screen.getByRole('button', { name: /Submit for Approval/ }));

    expect(await screen.findByText('Asha Rai appears more than once.')).toBeInTheDocument();
  });
});

/**
 * Phase MEMO-ENTERPRISE-FINAL-HARDENING.
 *
 * Every test below fails against the previous code. "+ Add more" appended
 * `{ title: '', body: '' }`, MemoSection.title is a required column, and the
 * submit gate checked only To and Subject — so the memo row was created, the
 * sections write was rejected, and the author was shown a sentence that named
 * neither the field nor the block.
 */
describe('CreateMemo — section titles (MEMO-ENTERPRISE-FINAL-HARDENING)', () => {
  beforeEach(() => vi.clearAllMocks());

  const addSection = () => fireEvent.click(screen.getByRole('button', { name: /Add more/i }));

  /** The matrix the server requires: one row, and the last row is an Approver. */
  const validWorkflow = async () => {
    await addEmployee('Bikash Thapa');
    fireEvent.change(screen.getByLabelText('Role type for Bikash Thapa'),
      { target: { value: 'approver' } });
  };

  it('"+ Add more" creates a titled block, never a blank one', () => {
    renderPage();
    addSection();
    expect(screen.getByLabelText('Section 3 title')).toHaveValue('Section 3');
    addSection();
    expect(screen.getByLabelText('Section 4 title')).toHaveValue('Section 4');
  });

  it('blanking a title disables both Save as draft and Submit', async () => {
    renderPage();
    fillDetails();
    await validWorkflow();
    const submit = screen.getByRole('button', { name: /Submit for Approval/i });
    await waitFor(() => expect(submit).toBeEnabled());

    fireEvent.change(screen.getByLabelText('Section 1 title'), { target: { value: '  ' } });
    expect(submit).toBeDisabled();
    expect(screen.getByRole('button', { name: /Save as draft/i })).toBeDisabled();
    expect(screen.getByText('Section 1 title is required.')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Section 1 title'), { target: { value: 'Background' } });
    await waitFor(() => expect(submit).toBeEnabled());
  });

  it('shows the server\'s own section error instead of a generic sentence', async () => {
    memoService.setSections.mockRejectedValueOnce({
      response: { data: { sections: [{}, { title: ['This field may not be blank.'] }] } },
    });
    renderPage();
    fillDetails();
    await validWorkflow();
    fireEvent.click(screen.getByRole('button', { name: /Submit for Approval/i }));
    expect(await screen.findByText('Section 2 title is required.')).toBeInTheDocument();
  });

  it('a retry after a failure reuses the draft instead of orphaning one', async () => {
    memoService.setSections
      .mockRejectedValueOnce({ response: { data: { sections: [{ title: ['This field may not be blank.'] }] } } })
      .mockResolvedValueOnce([]);
    renderPage();
    fillDetails();
    await validWorkflow();
    const submit = screen.getByRole('button', { name: /Submit for Approval/i });

    fireEvent.click(submit);
    await screen.findByText('Section 1 title is required.');
    expect(memoService.createMemo).toHaveBeenCalledTimes(1);

    fireEvent.click(submit);
    await waitFor(() => expect(memoService.sendForReview).toHaveBeenCalled());
    // THE POINT: still one memo row, not two. Eleven identical drafts in one
    // minute is what this prevents.
    expect(memoService.createMemo).toHaveBeenCalledTimes(1);
    expect(memoService.updateMemo).toHaveBeenCalledWith('m1', expect.any(Object));
  });
});

describe('CreateMemo — no duplicate or orphan drafts', () => {
  beforeEach(() => vi.clearAllMocks());

  it('five clicks in one turn create exactly one memo', async () => {
    /**
     * Found in the real browser, not here: five clicks dispatched inside a
     * single event-loop turn produced five POST /memos/ (one 201 and four 500s
     * against SQLite's numbering lock). `busy` could not stop them — it only
     * disables the button on the next render, which had not happened yet.
     */
    renderPage();
    fillDetails();
    await addEmployee('Bikash Thapa');
    fireEvent.change(screen.getByLabelText('Role type for Bikash Thapa'),
      { target: { value: 'approver' } });

    const submit = screen.getByRole('button', { name: /Submit for Approval/i });
    for (let i = 0; i < 5; i += 1) fireEvent.click(submit);

    await waitFor(() => expect(memoService.sendForReview).toHaveBeenCalled());
    expect(memoService.createMemo).toHaveBeenCalledTimes(1);
    expect(memoService.sendForReview).toHaveBeenCalledTimes(1);
  });
});
