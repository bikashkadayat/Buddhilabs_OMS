import React, { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import MemberEditor from './MemberEditor';
import MinuteStatusTrail from './MinuteStatusTrail';
import NextStepBanner from './NextStepBanner';
import AcknowledgementPanel from './AcknowledgementPanel';

/*
 * The minute screens, tested against the E-minute manual rather than against the
 * previous implementation.
 *
 * Two promises are worth pinning:
 *   1. attendance decides who is asked - only members PRESENT acknowledge (p.8), and
 *      the UI must not suggest otherwise;
 *   2. the banner offers the manual's two submit buttons and nothing resembling an
 *      approval, and only when the server said the caller may act.
 */

// The picker is tested with the memo module. Here it stands in as "a way to choose a
// person", with a distinct person per placeholder so the three member boxes cannot be
// confused with each other.
vi.mock('../memo/EmployeeSelector', () => ({
  default: ({ onSelect, placeholder }) => {
    const person = placeholder.startsWith('Add a member who attended')
      ? { id: 1, full_name: 'Asha Rai', designation: 'Board Member' }
      : placeholder.startsWith('Add a member who did not')
        ? { id: 2, full_name: 'Bikash Thapa', designation: 'Board Member' }
        : { id: 3, full_name: 'Chandra Gurung', designation: 'Consultant' };
    return (
      <button type="button" onClick={() => onSelect(person)}>{placeholder}</button>
    );
  },
}));

const Harness = ({ onRows }) => {
  const [rows, setRows] = useState([]);
  return (
    <MemberEditor rows={rows}
      onChange={(next) => { setRows(next); onRows?.(next); }} />
  );
};

describe('MemberEditor', () => {
  it('offers the manual\'s three groups, not a role dropdown', () => {
    render(<Harness />);
    expect(screen.getByText('Members Present')).toBeInTheDocument();
    expect(screen.getByText('Members Absent')).toBeInTheDocument();
    expect(screen.getByText('Invitee Members')).toBeInTheDocument();
    expect(screen.queryByRole('combobox')).toBeNull();
  });

  it('tags each person with the group they were added to', () => {
    const onRows = vi.fn();
    render(<Harness onRows={onRows} />);
    fireEvent.click(screen.getByRole('button', { name: /Add a member who attended/ }));
    fireEvent.click(screen.getByRole('button', { name: /Add a member who did not/ }));
    fireEvent.click(screen.getByRole('button', { name: /Add an invitee/ }));

    expect(onRows.mock.calls.at(-1)[0].map((r) => [r.full_name, r.attendance]))
      .toEqual([
        ['Asha Rai', 'present'],
        ['Bikash Thapa', 'absent'],
        ['Chandra Gurung', 'invitee'],
      ]);
  });

  it('says how many people the minute will actually be sent to', () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: /Add a member who attended/ }));
    fireEvent.click(screen.getByRole('button', { name: /Add a member who did not/ }));
    // One present, one absent: the minute goes to one person.
    expect(screen.getByText(/goes to the/)).toHaveTextContent('1 member');
  });
});

const minute = (over = {}) => ({
  status: 'draft', status_label: 'Draft', fro_name: '',
  can_send_for_review: false, can_send_for_acknowledgement: false,
  can_return_review: false, can_acknowledge: false, can_edit: false,
  acknowledgement: null, ...over,
});

const handlers = () => ({
  onSendForReview: vi.fn(), onSendForAcknowledgement: vi.fn(),
  onReturnReview: vi.fn(), onAcknowledge: vi.fn(), onEdit: vi.fn(),
});

describe('NextStepBanner', () => {
  it('never offers an approval', () => {
    const h = handlers();
    render(<NextStepBanner minute={minute({
      can_send_for_review: true, can_send_for_acknowledgement: true,
    })} {...h} />);
    expect(screen.queryByRole('button', { name: /Approve/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /Reject/i })).toBeNull();
  });

  it('offers the manual\'s two submit buttons on a draft', () => {
    const h = handlers();
    render(<NextStepBanner minute={minute({
      can_send_for_review: true, can_send_for_acknowledgement: true,
    })} {...h} />);

    fireEvent.click(screen.getByRole('button', { name: /Submit for Draft Review/ }));
    expect(h.onSendForReview).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /Submit for Acknowledge/ }));
    expect(h.onSendForAcknowledgement).toHaveBeenCalled();
  });

  it('will not submit a draft that has nobody present', () => {
    const h = handlers();
    render(<NextStepBanner minute={minute()} {...h} />);
    expect(screen.getByText(/Add at least one member present/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Submit for Acknowledge/ })).toBeNull();
  });

  it('puts Acknowledge in front of a member who owes one', () => {
    const h = handlers();
    render(<NextStepBanner minute={minute({
      status: 'pending_acknowledgement', status_label: 'Pending Acknowledgement',
      can_acknowledge: true,
    })} {...h} />);

    expect(screen.getByText('Please acknowledge this minute')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Acknowledge/ }));
    expect(h.onAcknowledge).toHaveBeenCalled();
  });

  it('offers nothing to somebody with no part to play', () => {
    const h = handlers();
    render(<NextStepBanner minute={minute({
      status: 'pending_acknowledgement', status_label: 'Pending Acknowledgement',
      acknowledgement: { total: 4, acknowledged: 1 },
    })} {...h} />);

    expect(screen.getByText('Waiting for members to acknowledge')).toBeInTheDocument();
    expect(screen.getByText(/1 of 4/)).toBeInTheDocument();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('gives the FRO both a submit and a return', () => {
    const h = handlers();
    render(<NextStepBanner minute={minute({
      status: 'draft_for_review', status_label: 'Draft For Review',
      can_return_review: true, can_send_for_acknowledgement: true,
    })} {...h} />);

    fireEvent.click(screen.getByRole('button', { name: /Return to author/ }));
    expect(h.onReturnReview).toHaveBeenCalled();
  });

  it('states the resting position of an archived minute', () => {
    render(<NextStepBanner minute={minute({
      status: 'archived', status_label: 'Archived',
      archived_at: '2026-08-14T05:00:00Z',
      acknowledgement: { total: 4, acknowledged: 4 },
    })} {...handlers()} />);
    expect(screen.getByText('Acknowledged and archived')).toBeInTheDocument();
  });
});

describe('MinuteStatusTrail', () => {
  it('marks where the minute has got to', () => {
    render(<MinuteStatusTrail stages={[
      { key: 'draft', label: 'Draft', state: 'done' },
      { key: 'acknowledgement', label: 'Acknowledgement', state: 'active' },
      { key: 'archived', label: 'Archived', state: 'pending' },
    ]} acknowledgement={{ total: 3, acknowledged: 1 }} />);

    expect(screen.getByText('Done')).toBeInTheDocument();
    expect(screen.getByText('Now')).toBeInTheDocument();
    expect(screen.getByText('1 of 3 acknowledged')).toBeInTheDocument();
  });
});

const participant = (over = {}) => ({
  id: 'p1', name: 'Asha Rai', designation: 'Board Member',
  department_label: 'Finance', attendance: 'present', attendance_label: 'Present',
  ack_status: 'pending', acknowledged_at: null, remarks: '', is_late: false, ...over,
});

describe('AcknowledgementPanel', () => {
  it('counts only the members present', () => {
    render(<AcknowledgementPanel
      summary={{ total: 2, acknowledged: 1, pending: 1, percent: 50, is_open: true }}
      participants={[
        participant({ ack_status: 'acknowledged',
          acknowledged_at: '2026-08-12T04:00:00Z' }),
        participant({ id: 'p2', name: 'Bikash Thapa' }),
        participant({ id: 'p3', name: 'Chandra Gurung', attendance: 'absent',
          attendance_label: 'Absent', ack_status: 'not_required' }),
      ]} />);

    const tally = screen.getByLabelText('Acknowledgement summary');
    expect(tally).toHaveTextContent('2 members present');
    expect(tally).toHaveTextContent('1 acknowledged');
    // The absent member is listed, but not counted and not chased.
    expect(screen.getByText('Chandra Gurung')).toBeInTheDocument();
    expect(screen.getByText('Not required')).toBeInTheDocument();
  });

  it('offers no decline — the manual has no such action', () => {
    render(<AcknowledgementPanel
      summary={{ total: 1, acknowledged: 0, pending: 1, percent: 0, is_open: true }}
      participants={[participant()]} canAcknowledge
      onAcknowledge={() => {}} />);
    expect(screen.queryByRole('button', { name: /Decline/i })).toBeNull();
    expect(screen.getByRole('button', { name: /Acknowledge/ })).toBeInTheDocument();
  });

  it('passes the remark through when acknowledging', () => {
    const onAcknowledge = vi.fn();
    render(<AcknowledgementPanel
      summary={{ total: 1, acknowledged: 0, pending: 1, percent: 0, is_open: true }}
      participants={[participant()]} canAcknowledge
      onAcknowledge={onAcknowledge} />);

    fireEvent.change(screen.getByLabelText('Acknowledgement remarks'),
      { target: { value: 'Read in full.' } });
    fireEvent.click(screen.getByRole('button', { name: /Acknowledge/ }));
    expect(onAcknowledge).toHaveBeenCalledWith('Read in full.');
  });

  it('renders nothing when the minute has no members', () => {
    const { container } = render(
      <AcknowledgementPanel summary={{ total: 0 }} participants={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
