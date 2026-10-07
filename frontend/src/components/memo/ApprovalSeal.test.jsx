import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import ApprovalSeal from './ApprovalSeal';

const CERT = {
  stamp: 'Approved',
  approved_by: 'Rajesh HR',
  designation: 'HR Manager',
  department: 'Human Resource Department',
  approved_at: '2026-08-12T12:27:01Z',
  archived_at: '2026-08-12T12:27:01Z',
  verification_id: '7016-798C-44AE',
};

describe('ApprovalSeal', () => {
  it('answers approved / by whom / when without the activity log', () => {
    render(<ApprovalSeal certificate={CERT} />);
    expect(screen.getByText('Approved')).toBeInTheDocument();
    expect(screen.getByText(/Rajesh HR/)).toBeInTheDocument();
    expect(screen.getByText(/HR Manager/)).toBeInTheDocument();
    expect(screen.getByText('Approved on')).toBeInTheDocument();
    expect(screen.getByText('7016-798C-44AE')).toBeInTheDocument();
  });

  it('is labelled as the approval certificate for assistive technology', () => {
    render(<ApprovalSeal certificate={CERT} />);
    expect(screen.getByLabelText('Approval certificate')).toBeInTheDocument();
  });

  /**
   * The banner asserts that a document is approved, so it must be impossible to
   * show over one that is not. The server withholds the certificate for an
   * in-flight memo and the component renders nothing rather than deciding for
   * itself from a status string.
   */
  it('renders nothing without a certificate', () => {
    const { container } = render(<ApprovalSeal certificate={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when the prop is absent entirely', () => {
    const { container } = render(<ApprovalSeal />);
    expect(container).toBeEmptyDOMElement();
  });

  it('omits the archive row for an approved-but-not-archived memo', () => {
    render(<ApprovalSeal certificate={{ ...CERT, archived_at: null }} />);
    expect(screen.queryByText('Archived on')).toBeNull();
    expect(screen.getByText('Approved on')).toBeInTheDocument();
  });

  it('omits the verification row when no ID was issued', () => {
    render(<ApprovalSeal certificate={{ ...CERT, verification_id: '' }} />);
    expect(screen.queryByText('Verification')).toBeNull();
  });

  it('does not print a dangling separator when there is no designation', () => {
    render(<ApprovalSeal certificate={{ ...CERT, designation: '—' }} />);
    expect(screen.getByText('Rajesh HR')).toBeInTheDocument();
    expect(screen.queryByText(/·\s*—/)).toBeNull();
  });
});
