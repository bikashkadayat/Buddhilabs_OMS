/**
 * Phase APM-03b — the goal editor.
 *
 * The specification's one hard rule for this screen is "Total Weight = 100%".
 * These pin BOTH halves of how that is handled: the total and the shortfall are
 * always stated, and the form still lets somebody type an objective that takes
 * the total past 100 — because objectives are written in whatever order they
 * come to mind, and a form that refuses the fourth until the first three are
 * re-weighted makes people do arithmetic before they may finish a thought. The
 * TRANSITION is the gate, and it lives on the server.
 */
import React from 'react';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/appraisalService', () => ({
  appraisalService: {
    addGoal: vi.fn(), updateGoal: vi.fn(), deleteGoal: vi.fn(),
  },
}));

import GoalEditor from '../../components/appraisal/GoalEditor';
import { appraisalService } from '../../services/appraisalService';
import { appraisal, goal } from './testFixtures';

const renderEditor = (props) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><GoalEditor {...props} /></MemoryRouter>
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  vi.clearAllMocks();
  appraisalService.addGoal.mockResolvedValue({});
  appraisalService.updateGoal.mockResolvedValue({});
  appraisalService.deleteGoal.mockResolvedValue({});
});

describe('the 100% rule', () => {
  it('confirms the total when the weights add up', () => {
    renderEditor({ appraisal: appraisal(), canEdit: true });
    expect(screen.getByRole('status')).toHaveTextContent('Your year adds up to 100%');
  });

  it('names the SHORTFALL, not just the total', async () => {
    // "Total weight 60%" leaves the reader to subtract. The number they need
    // is how much is left, so it is the number on screen.
    renderEditor({
      appraisal: appraisal({ goal_weight_total: 60, goals: [goal('One', 60)] }),
      canEdit: true,
    });
    expect(screen.getByRole('status'))
      .toHaveTextContent('add 40% more');
  });

  it('names the OVERAGE when the weights go past 100', () => {
    renderEditor({
      appraisal: appraisal({ goal_weight_total: 130 }), canEdit: true,
    });
    expect(screen.getByRole('status')).toHaveTextContent('that is 30% too much');
  });

  it('says in words what the consequence is, not only in a badge', () => {
    // A coloured badge with a number is not an instruction. Somebody has to be
    // told what will not happen until they fix it.
    renderEditor({
      appraisal: appraisal({ goal_weight_total: 60 }), canEdit: true,
    });
    expect(screen.getByRole('note')).toHaveTextContent(
      /need to add up to 100% before you can send them/i);
  });

  it('does not block adding a goal that overshoots', async () => {
    const user = userEvent.setup();
    renderEditor({
      appraisal: appraisal({ goal_weight_total: 100 }), canEdit: true,
    });
    await user.click(screen.getByRole('button', { name: /add goal/i }));
    await user.type(screen.getByLabelText(/^goal$/i), 'A third thing');
    await user.clear(screen.getByLabelText(/share of your year/i));
    await user.type(screen.getByLabelText(/share of your year/i), '30');
    await user.click(screen.getByRole('button', { name: /save goal/i }));

    expect(appraisalService.addGoal).toHaveBeenCalledWith(
      'a-1', expect.objectContaining({ objective: 'A third thing', weight: 30 }));
  });
});

describe('the fields the specification names', () => {
  it('offers goal, share of year, success and due date when adding', async () => {
    const user = userEvent.setup();
    renderEditor({ appraisal: appraisal(), canEdit: true });
    await user.click(screen.getByRole('button', { name: /add goal/i }));

    expect(screen.getByLabelText(/^goal$/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/share of your year/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/what success looks like/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/due date/i)).toBeInTheDocument();
  });

  it('keeps achievement separate from target when editing', async () => {
    // Overwriting the target with the outcome is how a year's objectives
    // quietly become whatever was delivered.
    const user = userEvent.setup();
    renderEditor({ appraisal: appraisal(), canEdit: true });
    await user.click(screen.getByRole('button',
      { name: /edit .*Deliver the quarterly return/i }));

    expect(screen.getByLabelText(/what success looks like/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/what happened/i)).toBeInTheDocument();
  });

  it('shows the evidence cited against THAT objective on the objective',
    () => {
      // The sixth field the specification lists for a goal. Evidence cited
      // against the appraisal as a whole must not appear here, or every
      // objective would look as though it had its own supporting figures.
      renderEditor({
        appraisal: appraisal({
          evidence_references: [
            { id: 'e-1', goal: 'g-Deliver the quarterly return', source: 'task',
              captured_at: '2026-08-31T10:00:00Z', note: 'The return itself.',
              metrics: [{ key: 'tasks_completed', label: 'Tasks Completed',
                value: 9 }] },
            { id: 'e-2', goal: null, source: 'task',
              captured_at: '2026-08-31T10:00:00Z', note: 'Whole year.',
              metrics: [{ key: 'tasks_assigned', label: 'Tasks Assigned',
                value: 12 }] },
          ],
        }),
        canEdit: false,
      });
      const rows = screen.getAllByRole('listitem');
      expect(within(rows[0]).getByText(/Tasks Completed: 9/))
        .toBeInTheDocument();
      expect(within(rows[0]).getByText(/The return itself/)).toBeInTheDocument();
      // The whole-appraisal citation belongs to neither objective.
      expect(screen.queryByText(/Tasks Assigned: 12/)).toBeNull();
      expect(within(rows[1]).queryByText(/cited/)).toBeNull();
    });

  it('renders the progress NUMBER beside the bar, never the bar alone', () => {
    // A bar with no number cannot be read by a screen reader or a printer.
    renderEditor({ appraisal: appraisal(), canEdit: false });
    const rows = screen.getAllByRole('listitem');
    expect(within(rows[0]).getByText('40%')).toBeInTheDocument();
  });
});

describe('who may edit', () => {
  it('offers no editing controls when the server says the caller may not', () => {
    renderEditor({ appraisal: appraisal(), canEdit: false });
    expect(screen.queryByRole('button', { name: /add goal/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /^edit/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /remove/i })).toBeNull();
  });

  it('names the objective in each row control, not just "Edit"', async () => {
    // A page can hold six of these. Six buttons all called "Edit" is six
    // identical announcements to a screen reader.
    renderEditor({ appraisal: appraisal(), canEdit: true });
    expect(screen.getByRole('button',
      { name: /edit .*Improve the archive process/i })).toBeInTheDocument();
    expect(screen.getByRole('button',
      { name: /remove .*Improve the archive process/i })).toBeInTheDocument();
  });
});
