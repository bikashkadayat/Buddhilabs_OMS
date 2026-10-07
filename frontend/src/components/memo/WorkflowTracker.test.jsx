import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import WorkflowTracker from './WorkflowTracker';

const STAGES = [
  { key: 'created', label: 'Created', state: 'done', at: '2026-08-12T08:30:00Z', actor: 'Deepak Employee' },
  { key: 'recommended', label: 'Recommended', state: 'done', at: '2026-08-12T09:10:00Z', actor: 'Sunita DeptHead' },
  { key: 'supported', label: 'Supported', state: 'active', at: null, actor: 'Manoj Support' },
  { key: 'approved', label: 'Approved', state: 'pending', at: null, actor: 'Rajesh HR' },
  { key: 'archived', label: 'Archived', state: 'pending', at: null, actor: '' },
];

describe('WorkflowTracker', () => {
  it('always draws the five hierarchy milestones', () => {
    render(<WorkflowTracker stages={STAGES} />);
    ['Created', 'Recommended', 'Supported', 'Approved', 'Archived'].forEach((label) => {
      expect(screen.getByText(label)).toBeInTheDocument();
    });
  });

  it('spells out each state rather than relying on colour', () => {
    render(<WorkflowTracker stages={STAGES} />);
    expect(screen.getAllByText('Completed')).toHaveLength(2);
    expect(screen.getByText('In progress')).toBeInTheDocument();
    expect(screen.getAllByText('Pending')).toHaveLength(2);
  });

  it('marks each node with its state class', () => {
    const { container } = render(<WorkflowTracker stages={STAGES} />);
    const steps = container.querySelectorAll('.memo-track-step');
    expect(steps).toHaveLength(5);
    expect(steps[0].className).toContain('is-done');
    expect(steps[2].className).toContain('is-active');
    expect(steps[3].className).toContain('is-pending');
  });

  it('names who each stage is with, and when a done stage happened', () => {
    render(<WorkflowTracker stages={STAGES} />);
    expect(screen.getByText('Sunita DeptHead')).toBeInTheDocument();
    expect(screen.getByText('Manoj Support')).toBeInTheDocument();
    // Two completed stages carry a timestamp; the rest do not.
    expect(document.querySelectorAll('time')).toHaveLength(2);
  });

  it('shows a stage the chain omitted as Not required, not Pending', () => {
    render(<WorkflowTracker stages={[
      ...STAGES.slice(0, 1),
      { key: 'recommended', label: 'Recommended', state: 'skipped', at: null, actor: '' },
      ...STAGES.slice(2),
    ]} />);
    // A memo routed straight to an approver is not forever waiting on a
    // recommendation it never asked for.
    expect(screen.getByText('Not required')).toBeInTheDocument();
    expect(document.querySelectorAll('.memo-track-step.is-skipped')).toHaveLength(1);
  });

  it('shows a rejection', () => {
    render(<WorkflowTracker stages={[
      ...STAGES.slice(0, 1),
      { key: 'recommended', label: 'Recommended', state: 'rejected', at: '2026-08-12T10:00:00Z', actor: 'Sunita DeptHead' },
      ...STAGES.slice(2),
    ]} />);
    expect(screen.getByText('Rejected')).toBeInTheDocument();
    expect(document.querySelectorAll('.memo-track-step.is-rejected')).toHaveLength(1);
  });

  it('renders nothing without stages', () => {
    const { container } = render(<WorkflowTracker stages={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
