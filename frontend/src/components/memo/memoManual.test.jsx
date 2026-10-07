import React, { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import MemoSectionsEditor from './MemoSectionsEditor';
import MemoSpecialActions from './MemoSpecialActions';
import { MemoTypeBadge } from './badges';
import { MEMO_TYPES, UNAVAILABILITY_REASONS, memoTypeLabel } from './memoLabels';

/*
 * The E-memo manual's surfaces, tested against the document.
 *
 * Two things are worth pinning: the content blocks behave like the manual's
 * "+ Add more" (a list of title/description pairs, each removable), and the
 * special-case actions are gated on the server's capability flags rather than
 * guessed at by the client.
 */

vi.mock('./RichTextEditor', () => ({
  default: ({ value, onChange, placeholder }) => (
    <textarea aria-label={placeholder || 'body'} value={value || ''}
      onChange={(e) => onChange?.(e.target.value)} />
  ),
}));

vi.mock('./EmployeeSelector', () => ({
  default: ({ onSelect, placeholder }) => (
    <button type="button"
      onClick={() => onSelect({ id: 7, full_name: 'Asha Rai', designation: 'Manager' })}>
      {placeholder}
    </button>
  ),
}));

const Harness = ({ onRows, initial }) => {
  const [sections, setSections] = useState(initial ?? [
    { title: 'Background', body: '' },
    { title: 'Recommendation', body: '' },
  ]);
  return (
    <MemoSectionsEditor sections={sections}
      onChange={(next) => { setSections(next); onRows?.(next); }} />
  );
};

describe('memo content blocks', () => {
  it('opens with the two blocks the manual\'s form opens with', () => {
    render(<Harness />);
    expect(screen.getByDisplayValue('Background')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Recommendation')).toBeInTheDocument();
  });

  it('"Add more" appends a title-and-description pair, already titled', () => {
    /**
     * THIS ASSERTION WAS INVERTED, and the reason matters more than the value.
     *
     * It used to pin `{ title: '', body: '' }` — the literal shape the control
     * produced. That shape is invalid: MemoSection.title is a required column,
     * so a memo containing a block from "+ Add more" was rejected by the server
     * unless the author happened to type a heading. The memo row had already
     * been created by then, so each attempt orphaned a draft; the local
     * database holds 29 attachment-less drafts and eleven copies of one subject
     * created inside a single minute.
     *
     * The test passed throughout, because it was asserting the defect.
     * A new block now arrives with a real title (Phase
     * MEMO-ENTERPRISE-FINAL-HARDENING); the author may still rename it, and
     * still may not blank it.
     */
    const onRows = vi.fn();
    render(<Harness onRows={onRows} />);
    fireEvent.click(screen.getByRole('button', { name: /Add more/ }));

    const rows = onRows.mock.calls.at(-1)[0];
    expect(rows).toHaveLength(3);
    expect(rows[2]).toEqual({ title: 'Section 3', body: '' });
  });

  it('the red X removes a block, including one of the defaults', () => {
    const onRows = vi.fn();
    render(<Harness onRows={onRows} />);
    fireEvent.click(screen.getByRole('button', { name: 'Remove Background' }));

    expect(onRows.mock.calls.at(-1)[0].map((s) => s.title)).toEqual(['Recommendation']);
  });

  it('keeps the list order, because it is the document order', () => {
    const onRows = vi.fn();
    render(<Harness onRows={onRows} />);
    fireEvent.change(screen.getAllByLabelText(/Section 1 title/)[0],
      { target: { value: 'Context' } });
    expect(onRows.mock.calls.at(-1)[0][0].title).toBe('Context');
    expect(onRows.mock.calls.at(-1)[0][1].title).toBe('Recommendation');
  });
});

describe('memo type', () => {
  it('offers exactly the manual\'s three values', () => {
    expect(MEMO_TYPES.map((t) => t.label))
      .toEqual(['GENERAL', 'CONFIDENTIAL', 'DRAFT']);
  });

  it('labels them as the manual writes them', () => {
    expect(memoTypeLabel('confidential')).toBe('CONFIDENTIAL');
  });

  it('padlocks only the type that restricts access', () => {
    const { container, rerender } = render(<MemoTypeBadge memo_type="confidential" />);
    expect(container.querySelector('svg')).toBeTruthy();
    rerender(<MemoTypeBadge memo_type="general" />);
    expect(container.querySelector('svg')).toBeNull();
  });
});

const wrap = (ui) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
};

const memo = (over = {}) => ({
  id: 'm1', can_edit: false, can_request_notes: false,
  can_manage_assignments: false, can_share_archive: false, ...over,
});

describe('memo special actions', () => {
  it('offers nothing to somebody with no permissions', () => {
    wrap(<MemoSpecialActions memo={memo()} />);
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('offers each control only on its own capability flag', () => {
    wrap(<MemoSpecialActions memo={memo({ can_request_notes: true })} />);
    expect(screen.getByRole('button', { name: /Add noted member/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Mark as Unavailable/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Add view access/ })).toBeNull();
  });

  it('lists the manual\'s five unavailable types, in its order', () => {
    wrap(<MemoSpecialActions memo={memo({ can_manage_assignments: true })} />);
    fireEvent.click(screen.getByRole('button', { name: /Mark as Unavailable/ }));

    const select = screen.getByLabelText('Select Unavailable type');
    expect([...select.options].map((o) => o.textContent)).toEqual([
      'Field/Site Visit', 'On Leave', 'Branch Visit', 'On Training',
      'On Conference/Meeting',
    ]);
  });

  it('the reason list matches the shared vocabulary', () => {
    expect(UNAVAILABILITY_REASONS.map((r) => r.label)).toEqual([
      'Field/Site Visit', 'On Leave', 'Branch Visit', 'On Training',
      'On Conference/Meeting',
    ]);
  });

  it('the upload modal carries the manual\'s File Name box', () => {
    wrap(<MemoSpecialActions memo={memo({ can_edit: true })} />);
    fireEvent.click(screen.getByRole('button', { name: /Add file/ }));

    expect(screen.getByLabelText('File Name')).toBeInTheDocument();
    expect(screen.getByText(/Accept only PDF, doc, docx, xls, xlsx, csv/))
      .toBeInTheDocument();
    // 10 MB since Phase MEMO-ATTACHMENT-SIZE-UPGRADE. The external E-memo
    // manual (p.6) still says 2 MB; the repo's own memo-guide.md already said
    // 10 MB, so the code was the odd one out rather than the guide.
    expect(screen.getByText(/larger than 10 MB/)).toBeInTheDocument();
  });

  it('will not submit a new approver without a person and a reason', () => {
    wrap(<MemoSpecialActions memo={memo({ can_manage_assignments: true })} />);
    fireEvent.click(screen.getByRole('button', { name: /Add new approver/ }));

    const submit = screen.getByRole('button', { name: /Submit to the new Approver/ });
    expect(submit).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: /Select the new approver/ }));
    expect(submit).toBeDisabled();          // still needs a reason

    fireEvent.change(screen.getByLabelText('Reason'),
      { target: { value: 'On leave until the 20th' } });
    expect(submit).toBeEnabled();
  });
});
