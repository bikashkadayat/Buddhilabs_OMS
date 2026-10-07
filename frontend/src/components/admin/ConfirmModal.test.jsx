import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ConfirmModal from './ConfirmModal';

describe('ConfirmModal', () => {
  it('disables confirm until the confirm word is typed exactly', () => {
    const onConfirm = vi.fn();
    render(<ConfirmModal title="Delete" confirmWord="DELETE" confirmLabel="Delete" onConfirm={onConfirm} onClose={() => {}} />);
    const btn = screen.getByRole('button', { name: 'Delete' });
    expect(btn).toBeDisabled();

    fireEvent.change(screen.getByLabelText('Type DELETE to confirm'), { target: { value: 'DELETE' } });
    expect(btn).toBeEnabled();
    fireEvent.click(btn);
    expect(onConfirm).toHaveBeenCalled();
  });

  it('requires a reason (>=5 chars) when requireReason is set', () => {
    const onConfirm = vi.fn();
    render(<ConfirmModal title="Reject" requireReason confirmLabel="Reject" onConfirm={onConfirm} onClose={() => {}} />);
    const btn = screen.getByRole('button', { name: 'Reject' });
    expect(btn).toBeDisabled();

    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'no' } });
    expect(btn).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'policy breach' } });
    expect(btn).toBeEnabled();
    fireEvent.click(btn);
    expect(onConfirm).toHaveBeenCalledWith('policy breach');
  });

  it('shows an impact summary when provided', () => {
    render(<ConfirmModal title="Approve" impact="This will affect 12 employees across 3 departments." onConfirm={() => {}} onClose={() => {}} />);
    expect(screen.getByText(/affect 12 employees/i)).toBeInTheDocument();
  });
});
