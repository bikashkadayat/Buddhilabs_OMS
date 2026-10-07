import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import MemoAttachmentPanel from '../components/memo/AttachmentPanel';

const file = {
  id: '015d197e', original_name: 'quotation.pdf', display_name: '',
  label: 'quotation.pdf', size: 69,
  uploaded_by: { id: 'u1', full_name: 'Sanjaya Poudel' },
  uploaded_at: '2026-09-04T10:00:00Z',
  url: '/api/v1/media/?p=x&sig=y',
  preview_url: '/api/v1/media/?p=x&sig=z',
};

describe('MemoAttachmentPanel render path', () => {
  it('renders a row for a real API payload', () => {
    const { container } = render(<MemoAttachmentPanel files={[file]} legacyUrl="" />);
    console.log('  HTML length:', container.innerHTML.length);
    console.log('  contains filename:', container.innerHTML.includes('quotation.pdf'));
    expect(screen.getByText('quotation.pdf')).toBeInTheDocument();
    expect(screen.getByText(/Attachments/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Preview/ })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Download/ })).toBeInTheDocument();
  });

  it('renders NOTHING when there are no files — the null path', () => {
    const { container } = render(<MemoAttachmentPanel files={[]} legacyUrl="" />);
    console.log('  empty-case HTML:', JSON.stringify(container.innerHTML));
    expect(container.innerHTML).toBe('');
  });
});
