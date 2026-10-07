import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import WorkflowStatusCard from './WorkflowStatusCard';

/**
 * Phase MEMO-WORKFLOW-FINAL-ENTERPRISE.
 *
 * The page already showed which stage (tracker), who did what and when
 * (matrix table) and what had happened (timeline). None of them answered, at a
 * glance, the question people open a memo to ask: who is it sitting with, and
 * since when.
 */
const step = (over = {}) => ({
  sequence: 1, role_type: 'reviewer', status: 'pending',
  display_name: 'Prashanta Acharya', designation: 'Officer',
  activated_at: null, acted_at: null, ...over,
});

const hoursAgo = (n) => new Date(Date.now() - n * 3600_000).toISOString();

describe('WorkflowStatusCard', () => {
  it('renders nothing when there is no workflow', () => {
    const { container } = render(<WorkflowStatusCard steps={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('names the current stage, the person, and how long they have held it', () => {
    render(<WorkflowStatusCard status="under_review" steps={[
      step({ sequence: 1, role_type: 'reviewer', status: 'completed', acted_at: hoursAgo(50) }),
      step({ sequence: 2, role_type: 'recommender', status: 'active',
             display_name: 'Bikash Kadayat', activated_at: hoursAgo(49) }),
      step({ sequence: 3, role_type: 'approver', status: 'pending',
             display_name: 'Sanjaya Poudel' }),
    ]} />);
    expect(screen.getByText('Recommender')).toBeInTheDocument();
    expect(screen.getByText(/step 2 of 3/i)).toBeInTheDocument();
    expect(screen.getByText('Bikash Kadayat')).toBeInTheDocument();
    expect(screen.getByText(/2 days/)).toBeInTheDocument();
    expect(screen.getByText(/Approver — Sanjaya Poudel/)).toBeInTheDocument();
  });

  it('says the next step is Archive when nothing follows', () => {
    render(<WorkflowStatusCard status="supported" steps={[
      step({ sequence: 1, role_type: 'approver', status: 'active',
             activated_at: hoursAgo(1) }),
    ]} />);
    expect(screen.getByText('Archive')).toBeInTheDocument();
  });

  it('does not go blank once the chain is finished', () => {
    // A card that empties at the end reads as a data failure, so the finished
    // state is stated rather than rendered as absence.
    render(<WorkflowStatusCard status="archived" steps={[
      step({ sequence: 1, role_type: 'reviewer', status: 'completed',
             acted_at: hoursAgo(5) }),
      step({ sequence: 2, role_type: 'approver', status: 'completed',
             display_name: 'Sanjaya Poudel', acted_at: hoursAgo(2) }),
    ]} />);
    expect(screen.getByText('Archived')).toBeInTheDocument();
    expect(screen.getByText(/no step is waiting/i)).toBeInTheDocument();
    expect(screen.getByText(/Sanjaya Poudel · Approver/)).toBeInTheDocument();
  });

  it('tolerates a missing activation timestamp', () => {
    // `activated_at` is null on a step that was never activated; the row is
    // omitted rather than rendering "NaN days".
    render(<WorkflowStatusCard status="under_review" steps={[
      step({ status: 'active', activated_at: null }),
    ]} />);
    expect(screen.queryByText(/pending since/i)).toBeNull();
    expect(screen.getByText('Prashanta Acharya')).toBeInTheDocument();
  });
});
