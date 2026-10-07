/**
 * Task governance in the UI (Phase TASK-GOVERNANCE-HARDENING).
 *
 * What is worth pinning here is that the page TELLS THE TRUTH ABOUT WHY a
 * button will not work. The engine refuses a blocked task's transitions; if the
 * page silently hid the button, or offered it and let the request fail, the
 * person would be left guessing at a rule the system is enforcing perfectly
 * well. So: the block is stated, it names what is in the way, and the
 * dependency rows carry the prerequisite's live status.
 *
 * The milestone track is pinned for the opposite reason: an unreached milestone
 * must be DRAWN, not omitted, because a gap is information.
 */
import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import TaskDetail from './TaskDetail';
import MilestoneTrack from '../../components/task/MilestoneTrack';
import DepartmentManagement from '../admin/leaves/DepartmentManagement';

vi.mock('../../hooks/useAuth', () => ({ useAuth: vi.fn() }));
vi.mock('../../services/taskService', () => ({
  taskService: {
    getTask: vi.fn(), getTasks: vi.fn(), searchEmployees: vi.fn(),
    getTemplates: vi.fn(), addDependency: vi.fn(), removeDependency: vi.fn(),
    start: vi.fn(), submitForReview: vi.fn(), markDone: vi.fn(), accept: vi.fn(),
    approve: vi.fn(), requestRework: vi.fn(), close: vi.fn(), block: vi.fn(),
    unblock: vi.fn(), cancel: vi.fn(), assign: vi.fn(), updateProgress: vi.fn(),
    requestClarification: vi.fn(), setChecklist: vi.fn(), tickChecklistItem: vi.fn(),
    addComment: vi.fn(), editComment: vi.fn(), uploadAttachments: vi.fn(),
    addLink: vi.fn(), removeAttachment: vi.fn(), flagAttachment: vi.fn(),
    getDownloadLog: vi.fn(), applyTemplate: vi.fn(), saveAsTemplate: vi.fn(),
  },
}));
vi.mock('../../services/adminLeaveService', () => ({
  adminLeaveService: {
    getDepartments: vi.fn(), getUsers: vi.fn(), createDepartment: vi.fn(),
    updateDepartment: vi.fn(), deleteDepartment: vi.fn(),
  },
}));

import { taskService } from '../../services/taskService';
import { adminLeaveService } from '../../services/adminLeaveService';
import { useAuth } from '../../hooks/useAuth';

const CAPS = {
  can_view: true, can_edit: false, can_delete: false, can_manage_assignees: false,
  can_assign: false, can_accept: false, can_request_clarification: false,
  can_update_progress: true, can_submit_for_review: false, can_mark_done: false,
  can_review: false, can_close: false, can_block: false, can_unblock: false,
  can_cancel: false, can_comment: true, can_upload: false,
  can_manage_checklist: false, can_tick_checklist: false,
  can_manage_dependencies: true,
};

const blocker = (overrides = {}) => ({
  id: 'd1', kind: 'blocked_by', kind_label: 'Blocked by', note: '',
  task: 't1', task_number: 'NIFN-TSK-2083-0001', task_title: 'This one',
  task_status_label: 'Accepted',
  depends_on: 't2', depends_on_number: 'NIFN-TSK-2083-0007',
  depends_on_title: 'Switch delivery', depends_on_status: 'in_progress',
  depends_on_status_label: 'In Progress', is_satisfied: false,
  created_by_name: 'Hod Person', created_at: '2026-09-01T09:00:00Z',
  ...overrides,
});

const task = (overrides = {}) => ({
  id: 't1', task_number: 'NIFN-TSK-2083-0001', title: 'Rack the new switch',
  description: '', summary: '', status: 'accepted', status_label: 'Accepted',
  task_type: 'department', task_type_label: 'Department Task',
  priority: 'medium', priority_label: 'Medium', due_date: null, is_overdue: false,
  progress_percent: 0, checklist_percent: null, department_name: 'ICT',
  created_by_name: 'Employee Person', reviewer_name: 'Hod Person',
  owner_name: 'Employee Person', assignee_names: ['Employee Person'],
  checklist_done: 0, checklist_total: 0, blocked_reason: '', clarification_note: '',
  assignees: [], checklist: [], checklist_groups: [], attachments: [], evidence: [],
  removed_attachments: [], comments: [], timeline: [], progress_is_auto: true,
  template_name: null, created_at: '2026-09-01T08:00:00Z', closed_at: null,
  dependencies: [], dependents: [],
  milestones: [
    { key: 'created', label: 'Created', at: '2026-09-01T08:00:00Z' },
    { key: 'started', label: 'Started', at: null },
    { key: 'reviewed', label: 'Reviewed', at: null, submitted_at: null },
    { key: 'completed', label: 'Completed', at: null },
  ],
  capabilities: CAPS,
  ...overrides,
});

const renderDetail = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/tasks/t1']}>
        <Routes><Route path="/tasks/:id" element={<TaskDetail />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

const ready = () => waitFor(() =>
  expect(screen.getByText('Task Information')).toBeInTheDocument());

describe('dependencies on the task page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuth.mockReturnValue({ user: { id: 'u1' }, role: 'maker' });
    taskService.searchEmployees.mockResolvedValue([]);
    taskService.getTemplates.mockResolvedValue([]);
    taskService.getTasks.mockResolvedValue({ results: [] });
  });

  it('says the task is blocked, and names what it is waiting for', async () => {
    taskService.getTask.mockResolvedValue(task({ dependencies: [blocker()] }));
    renderDetail();
    await ready();

    const notice = screen.getByText(/Waiting on other work/).closest('div');
    expect(notice.textContent).toContain('NIFN-TSK-2083-0007');
    // The prerequisite's LIVE status, so "waiting" is legible rather than a claim.
    expect(notice.textContent).toContain('In Progress');
  });

  it('says nothing about blocking when nothing is in the way', async () => {
    taskService.getTask.mockResolvedValue(task());
    renderDetail();
    await ready();
    expect(screen.queryByText(/Waiting on other work/)).toBeNull();
    expect(screen.getByText('Nothing is holding this task up.')).toBeInTheDocument();
  });

  it('keeps a satisfied dependency on the page instead of dropping it', async () => {
    // A row that vanished when it was met would leave the reader unsure it
    // ever existed - and the banner must go, because the gate has lifted.
    taskService.getTask.mockResolvedValue(task({
      dependencies: [blocker({ is_satisfied: true, depends_on_status: 'completed',
        depends_on_status_label: 'Completed' })],
    }));
    renderDetail();
    await ready();
    expect(screen.getByText(/Switch delivery/)).toBeInTheDocument();
    expect(screen.queryByText(/Waiting on other work/)).toBeNull();
  });

  it('shows what this task is blocking, with no controls on it', async () => {
    taskService.getTask.mockResolvedValue(task({
      dependents: [blocker({ id: 'd9', task: 't5', task_number: 'NIFN-TSK-2083-0055',
        task_title: 'Patch the rack', task_status_label: 'Assigned' })],
    }));
    renderDetail();
    await ready();
    expect(screen.getByText('Blocking')).toBeInTheDocument();
    expect(screen.getByText(/Patch the rack/)).toBeInTheDocument();
    expect(screen.queryByRole('button',
      { name: /Stop waiting for NIFN-TSK-2083-0055/ })).toBeNull();
  });

  it('offers no dependency controls without the capability', async () => {
    taskService.getTask.mockResolvedValue(task({
      dependencies: [blocker()],
      capabilities: { ...CAPS, can_manage_dependencies: false },
    }));
    renderDetail();
    await ready();
    expect(screen.queryByLabelText('Search for the task this one waits for')).toBeNull();
    expect(screen.queryByRole('button',
      { name: /Stop waiting for NIFN-TSK-2083-0007/ })).toBeNull();
  });

  it('removes a dependency through the API when asked', async () => {
    taskService.getTask.mockResolvedValue(task({ dependencies: [blocker()] }));
    taskService.removeDependency.mockResolvedValue({});
    renderDetail();
    await ready();
    fireEvent.click(screen.getByRole('button',
      { name: 'Stop waiting for NIFN-TSK-2083-0007' }));
    await waitFor(() =>
      expect(taskService.removeDependency).toHaveBeenCalledWith('t1', 'd1'));
  });
});

describe('the milestone track', () => {
  it('draws a milestone that has not happened rather than omitting it', () => {
    render(<MilestoneTrack milestones={task().milestones} />);
    for (const label of ['Created', 'Started', 'Reviewed', 'Completed']) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    // Three of the four are not reached, and each says so.
    expect(screen.getAllByText('Not yet')).toHaveLength(3);
  });

  it('renders nothing at all when the server sent no milestones', () => {
    const { container } = render(<MilestoneTrack />);
    expect(container.firstChild).toBeNull();
  });
});

describe('departments without a head', () => {
  const renderDepartments = () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={qc}>
        <MemoryRouter><DepartmentManagement /></MemoryRouter>
      </QueryClientProvider>,
    );
  };

  beforeEach(() => {
    vi.clearAllMocks();
    adminLeaveService.getUsers.mockResolvedValue([]);
  });

  it('names them, and says what it costs, on the page where it is fixed',
    async () => {
      adminLeaveService.getDepartments.mockResolvedValue([
        { id: 'd1', name: 'ICT Department', code: 'ICT', head: 'u1',
          head_name: 'Bikash Kadayat', member_count: 4, is_active: true },
        { id: 'd2', name: 'Operational Department', code: 'OPS', head: null,
          head_name: '', member_count: 1, is_active: true },
      ]);
      renderDepartments();
      await waitFor(() => expect(
        screen.getByText(/1 department has no Department Head assigned:/))
        .toBeInTheDocument());
      const banner = screen.getByText(
        /1 department has no Department Head assigned:/).closest('p');
      expect(banner.textContent).toContain('Operational Department');
      // What the gap COSTS, corrected in OMS-FINAL-FREEZE-HARDENING: it no
      // longer claims tasks are refused, because they are not.
      expect(banner.textContent).toContain('not affected');
      expect(banner.textContent).toContain('leave routing');
      expect(banner.textContent).not.toContain('refused');
      expect(banner.textContent).not.toContain('ICT Department');
    });

  it('counts a head whose account is closed as no head at all', async () => {
    // The server counts it that way (leaves.governance): the work routes
    // nowhere either way, and a page that disagreed with the health board about
    // the same department would teach people to believe neither.
    adminLeaveService.getDepartments.mockResolvedValue([
      { id: 'd1', name: 'ICT Department', code: 'ICT', head: 'u1',
        head_name: 'Bikash Kadayat', head_is_active: false, member_count: 4,
        is_active: true },
    ]);
    renderDepartments();
    await waitFor(() => expect(
      screen.getByText(/1 department has no Department Head assigned:/))
      .toBeInTheDocument());
    expect(screen.getByText(/account inactive, so nobody can act/))
      .toBeInTheDocument();
  });

  it('says nothing when every department has one', async () => {
    adminLeaveService.getDepartments.mockResolvedValue([
      { id: 'd1', name: 'ICT Department', code: 'ICT', head: 'u1',
        head_name: 'Bikash Kadayat', head_is_active: true, member_count: 4,
        is_active: true },
    ]);
    renderDepartments();
    await waitFor(() =>
      expect(screen.getByText('Bikash Kadayat')).toBeInTheDocument());
    expect(screen.queryByText(/no head/)).toBeNull();
  });
});
