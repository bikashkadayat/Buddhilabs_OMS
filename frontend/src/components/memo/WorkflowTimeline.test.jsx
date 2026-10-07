import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import WorkflowTimeline from './WorkflowTimeline';

const ENTRIES = [
  {
    kind: 'history', id: 'h1', sequence: 1, action: 'sent_for_review',
    label: 'Sent for Review', actor_name: 'Asha Rai', actor_id: 'e1',
    designation: 'Officer', at: '2026-08-12T08:30:00Z', remarks: '', state: 'done',
  },
  {
    kind: 'history', id: 'h2', sequence: 2, action: 'reviewed',
    label: 'Reviewed', actor_name: 'Bikash Thapa', actor_id: 'e2',
    designation: 'Senior Officer', at: '2026-08-12T09:30:00Z',
    remarks: 'Figures reconcile.', state: 'done',
  },
  {
    kind: 'upcoming', id: 's3', sequence: 3, action: 'supporter',
    label: 'Awaiting Supporter', actor_name: 'Chandra Shah', actor_id: 'e3',
    designation: 'Manager', at: null, remarks: '', state: 'active',
  },
  {
    kind: 'upcoming', id: 's4', sequence: 4, action: 'approver',
    label: 'Approver (queued)', actor_name: 'Dipesh Karki', actor_id: 'e4',
    designation: 'Director', at: null, remarks: '', state: 'pending',
  },
];

describe('WorkflowTimeline', () => {
  it('renders an empty state when there is no activity', () => {
    render(<WorkflowTimeline entries={[]} />);
    expect(screen.getByText('No workflow activity yet.')).toBeInTheDocument();
  });

  it('renders completed events with their actor, label and remarks', () => {
    render(<WorkflowTimeline entries={ENTRIES} />);
    expect(screen.getByText('Sent for Review')).toBeInTheDocument();
    expect(screen.getByText('Reviewed')).toBeInTheDocument();
    expect(screen.getByText('Bikash Thapa')).toBeInTheDocument();
    expect(screen.getByText('“Figures reconcile.”')).toBeInTheDocument();
  });

  it('renders steps that have not happened yet, with who they wait on', () => {
    render(<WorkflowTimeline entries={ENTRIES} />);
    // This is what the previous history-only timeline could not express: nothing
    // existed in the database for a step until after someone acted on it.
    expect(screen.getByText('Awaiting Supporter')).toBeInTheDocument();
    expect(screen.getByText('Chandra Shah')).toBeInTheDocument();
    expect(screen.getByText('Approver (queued)')).toBeInTheDocument();
    expect(screen.getByText('Dipesh Karki')).toBeInTheDocument();
  });

  it('marks the outstanding entries so they read as not-yet-done', () => {
    const { container } = render(<WorkflowTimeline entries={ENTRIES} />);
    const items = container.querySelectorAll('.memo-tl-item');
    expect(items).toHaveLength(4);
    expect(items[1].className).not.toContain('is-upcoming');
    expect(items[2].className).toContain('is-upcoming');
    expect(items[2].className).toContain('is-active');
    expect(items[3].className).toContain('is-upcoming');
    expect(items[3].className).not.toContain('is-active');
  });

  it('shows a clock time for completed events and a dash for outstanding ones', () => {
    render(<WorkflowTimeline entries={ENTRIES} />);
    // Two outstanding steps have no timestamp yet.
    expect(screen.getAllByText('—')).toHaveLength(2);
  });
});
