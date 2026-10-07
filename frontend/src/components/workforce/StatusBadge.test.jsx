import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import StatusBadge, { RequestBadge } from './StatusBadge';
import { REQUEST_STATUS_META, STATUS_META, statusLabel } from './statusMeta';

// Mirrors attendance.models.Attendance.Status on the backend. If the two ever
// drift, this test is where it shows up rather than as a blank chip in a table.
const BACKEND_STATUSES = [
  'present', 'absent', 'late', 'half_day', 'on_leave', 'holiday', 'wfh',
];

describe('StatusBadge', () => {
  it('renders every status the backend can produce', () => {
    BACKEND_STATUSES.forEach((status) => {
      const { unmount } = render(<StatusBadge status={status} />);
      expect(screen.getByText(STATUS_META[status].label)).toBeInTheDocument();
      unmount();
    });
  });

  it('covers Phase 8 work-from-home explicitly', () => {
    render(<StatusBadge status="wfh" />);
    expect(screen.getByText('Work From Home')).toBeInTheDocument();
  });

  it('renders a status it has never seen legibly instead of blank', () => {
    render(<StatusBadge status="sabbatical_leave" />);
    expect(screen.getByText('sabbatical leave')).toBeInTheDocument();
  });

  it('renders an em dash for a missing status', () => {
    expect(statusLabel(undefined)).toBe('—');
  });

  it('has a meta entry for every backend status', () => {
    BACKEND_STATUSES.forEach((status) => {
      expect(STATUS_META[status]).toBeDefined();
      expect(STATUS_META[status].tone).toBeTruthy();
    });
  });
});

describe('RequestBadge', () => {
  it('renders every workflow stage', () => {
    Object.entries(REQUEST_STATUS_META).forEach(([status, meta]) => {
      const { unmount } = render(<RequestBadge status={status} />);
      expect(screen.getByText(meta.label)).toBeInTheDocument();
      unmount();
    });
  });

  it('names the two approval stages distinctly', () => {
    expect(REQUEST_STATUS_META.pending.label).toContain('Dept Head');
    expect(REQUEST_STATUS_META.manager_approved.label).toContain('HR');
  });
});
