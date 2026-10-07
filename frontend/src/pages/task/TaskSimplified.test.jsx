/**
 * The simplified task (Phase TASK-SIMPLIFICATION).
 *
 * This file replaces TaskAsanaModel.test.jsx, which pinned the three task kinds
 * - Personal, Department, Assigned - and the rules about which roles could
 * raise which. All of that is gone. What is left is worth pinning precisely
 * because it is now the WHOLE model:
 *
 *   1. One task. Anybody may raise one, for anybody.
 *   2. The creator picks the assignee and the reviewer, and the reviewer is
 *      pre-filled with their department head where the server knows of one.
 *   3. The reviewer cannot be an assignee - said before the request, not only
 *      after it, because an error you can see coming is cheaper than one you
 *      are told about afterwards.
 */
import React from 'react';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import TaskForm from './TaskForm';
import TaskDetail from './TaskDetail';
import TaskCard from '../../components/task/TaskCard';

vi.mock('../../hooks/useAuth', () => ({ useAuth: vi.fn() }));
// The autosave engine behind the form (Phase TASK-SUBTASKS): nothing here
// should reach a draft endpoint or IndexedDB.
vi.mock('../../services/draftService', () => ({
  draftService: {
    saveDraft: vi.fn(() => Promise.resolve({ version: 1, saved_at: '2026-09-01T10:00:00Z' })),
    getDraft: vi.fn(() => Promise.resolve(null)),
    discardDraft: vi.fn(() => Promise.resolve()),
    markRecovered: vi.fn(() => Promise.resolve()),
  },
}));
vi.mock('../../services/draftStore', () => ({
  putSnapshot: vi.fn(() => Promise.resolve(true)),
  getSnapshot: vi.fn(() => Promise.resolve(null)),
  deleteSnapshot: vi.fn(() => Promise.resolve(true)),
  clearUser: vi.fn(() => Promise.resolve(0)),
  listUser: vi.fn(() => Promise.resolve([])),
  isSupported: () => true,
}));
// The rich editor is TipTap behind a lazy import; a textarea stands in for it
// so the form's own behaviour is what is under test.
vi.mock('../../components/memo/RichTextEditor', () => ({
  default: ({ value, onChange, ariaLabel, readOnly }) => (readOnly
    ? <div data-testid="rich-view">{value}</div>
    : <textarea aria-label={ariaLabel || 'Body'} value={value}
        onChange={(e) => onChange?.(e.target.value)} />),
}));
vi.mock('../../services/taskService', () => ({
  taskService: {
    getTask: vi.fn(), createTask: vi.fn(), updateTask: vi.fn(),
    createSubtask: vi.fn(), updateSubtask: vi.fn(), deleteSubtask: vi.fn(),
    completeSubtask: vi.fn(), reorderSubtasks: vi.fn(), downloadAttachment: vi.fn(),
    getFilterOptions: vi.fn(), searchEmployees: vi.fn(), getTemplates: vi.fn(),
    submitForReview: vi.fn(), accept: vi.fn(), start: vi.fn(), getTasks: vi.fn(),
    approve: vi.fn(), requestRework: vi.fn(), close: vi.fn(), block: vi.fn(),
    unblock: vi.fn(), cancel: vi.fn(), assign: vi.fn(), updateProgress: vi.fn(),
    requestClarification: vi.fn(), setChecklist: vi.fn(), tickChecklistItem: vi.fn(),
    addComment: vi.fn(), editComment: vi.fn(), uploadAttachments: vi.fn(),
    addLink: vi.fn(), removeAttachment: vi.fn(), flagAttachment: vi.fn(),
    getDownloadLog: vi.fn(), applyTemplate: vi.fn(), saveAsTemplate: vi.fn(),
    addDependency: vi.fn(), removeDependency: vi.fn(),
  },
}));

import { taskService } from '../../services/taskService';
import { draftService } from '../../services/draftService';
import { useAuth } from '../../hooks/useAuth';

const HEAD = { id: 'head-1', full_name: 'Bikash Kadayat' };
const ME = { id: 'u1', full_name: 'Prashanta Acharya' };
const WORKER = { id: 'worker-1', full_name: 'Kiran Maharjan',
  designation: 'Officer', department: 'ICT' };

const options = (overrides = {}) => ({
  departments: [], assignees: [], reviewers: [], statuses: [], priorities: [],
  default_assignee: ME,
  default_reviewer: HEAD,
  ...overrides,
});

const renderForm = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/tasks/create']}>
        <Routes><Route path="/tasks/create" element={<TaskForm />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

const formReady = () => waitFor(() =>
  expect(screen.getByLabelText('Search employees to assign')).toBeInTheDocument());

/**
 * Pick somebody from the search results.
 *
 * Deliberately scoped to the results list: the chosen person's row carries the
 * same name, and a bare getByText would match whichever the DOM happened to
 * reach first - which is how the first version of this test clicked a chip and
 * "passed" without ever adding an assignee.
 *
 * Assignee and reviewer have their OWN pickers since
 * Phase TASK-CREATE-UI-POLISH - there is no mode to switch first - so `as`
 * now selects which card's search box to type into.
 */
const pickFromResults = async (name, as = 'assignee') => {
  const label = as === 'reviewer'
    ? 'Search employees to review' : 'Search employees to assign';
  fireEvent.change(screen.getByLabelText(label),
    { target: { value: name.split(' ')[0].toLowerCase() } });
  const results = await screen.findByRole('list', { name: `${label} results` });
  const hit = await waitFor(() => {
    const button = [...results.querySelectorAll('button')]
      .find((b) => b.textContent.includes(name));
    expect(button).toBeTruthy();
    return button;
  });
  fireEvent.click(hit);
};

describe('raising a task', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuth.mockReturnValue({ user: { id: 'u1' }, role: 'maker' });
    // Both people are findable: the creator is the default assignee, and the
    // tests that build a conflict have to be able to name them as reviewer too.
    taskService.searchEmployees.mockResolvedValue([
      WORKER, { ...ME, designation: 'Officer', department: 'ICT' }]);
    taskService.getFilterOptions.mockResolvedValue(options());
  });

  it('starts with the creator doing the task, ready to submit', async () => {
    // Most tasks a person writes are their own, and the field is mandatory - a
    // default that is right most of the time is one click instead of three.
    renderForm();
    await formReady();
    await waitFor(() =>
      expect(screen.getByText('Prashanta Acharya')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /create/i })).not.toBeDisabled();
  });

  it('will not create a task once the assignee is taken off it', async () => {
    // A default, not a rule: it can be removed - and then the button stops,
    // because Assigned To is mandatory.
    renderForm();
    await formReady();
    await waitFor(() =>
      expect(screen.getByText('Prashanta Acharya')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Remove Prashanta Acharya' }));

    expect(screen.getByRole('button', { name: /create/i })).toBeDisabled();
    expect(screen.getByText(/Choose who is doing this task/)).toBeInTheDocument();
    expect(taskService.createTask).not.toHaveBeenCalled();
  });

  it('offers the people pickers to an ordinary employee', async () => {
    // The kinds are gone, and with them the rule that an Employee could not
    // name anybody else. Everybody gets the same form.
    renderForm();
    await formReady();
    expect(screen.queryByText(/kind of task/i)).toBeNull();
    // BOTH pickers, each in its own card (Phase TASK-CREATE-UI-POLISH). This
    // used to assert the mode dropdown that switched one box between the two
    // jobs; the rule it is testing — an Employee may name anybody for either
    // role — is the same.
    expect(screen.getByLabelText('Search employees to assign')).toBeInTheDocument();
    expect(screen.getByLabelText('Search employees to review')).toBeInTheDocument();
  });

  it('pre-fills the reviewer with the department head the server named',
    async () => {
      renderForm();
      await formReady();
      await waitFor(() =>
        expect(screen.getByText('Bikash Kadayat')).toBeInTheDocument());
    });

  it('asks for a reviewer when the server suggests none', async () => {
    // Phase TASK-REVIEWER-SELECTION: the field is required, and nothing routes
    // it afterwards - so with no suggestion the form must say what is missing
    // and refuse to submit.
    taskService.getFilterOptions.mockResolvedValue(
      options({ default_reviewer: null }));
    renderForm();
    await formReady();
    expect(screen.getByText(/Choose who approves this work/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /create/i })).toBeDisabled();
  });

  it('enables the button once a reviewer is chosen', async () => {
    taskService.getFilterOptions.mockResolvedValue(
      options({ default_reviewer: null }));
    renderForm();
    await formReady();
    await pickFromResults('Kiran Maharjan', 'reviewer');
    expect(screen.getByRole('button', { name: /create/i })).not.toBeDisabled();
  });

  it('keeps the suggested reviewer editable', async () => {
    // The department head is a suggestion, never a requirement: it can be
    // removed and replaced with any active user.
    renderForm();
    await formReady();
    await waitFor(() =>
      expect(screen.getByText('Bikash Kadayat')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Remove reviewer' }));
    await pickFromResults('Kiran Maharjan', 'reviewer');
    expect(screen.getByRole('button', { name: /create/i })).not.toBeDisabled();
  });

  it('sends the assignee and the reviewer the creator chose', async () => {
    taskService.createTask.mockResolvedValue({ id: 't9' });
    renderForm();
    await formReady();

    fireEvent.change(screen.getByPlaceholderText('What has to be done?'),
      { target: { value: 'Rack the new switch' } });
    // Take the default off and name somebody else: the creator may assign the
    // task to any active user.
    await waitFor(() =>
      expect(screen.getByText('Prashanta Acharya')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Remove Prashanta Acharya' }));
    await pickFromResults('Kiran Maharjan');
    fireEvent.click(screen.getByRole('button', { name: /create/i }));

    await waitFor(() => expect(taskService.createTask).toHaveBeenCalled());
    const payload = taskService.createTask.mock.calls[0][0];
    expect(payload.assignee_ids).toEqual([WORKER.id]);
    expect(payload.reviewer).toBe(HEAD.id);
    // The kind went with the three kinds.
    expect(payload).not.toHaveProperty('task_type');
  });

  it('starts the description from the brief template and sends it as HTML',
    async () => {
      // Phase TASK-SUBTASKS: the description is rich text now, and a new task
      // opens on the headings a brief answers rather than on an empty box.
      taskService.createTask.mockResolvedValue({ id: 't9' });
      renderForm();
      await formReady();
      // Named for what it is: a screen reader saying "Memo body" on a task
      // page is a wrong answer.
      const editor = screen.getByLabelText('Task description');
      expect(editor.value).toContain('<h2>Task Goal</h2>');
      expect(editor.value).toContain('<h2>Success Criteria</h2>');
      fireEvent.change(screen.getByPlaceholderText('What has to be done?'),
        { target: { value: 'Rack the new switch' } });
      fireEvent.change(editor, { target: { value: '<h2>Task Goal</h2><p>Rack it.</p>' } });
      fireEvent.click(screen.getByRole('button', { name: /create/i }));

      await waitFor(() => expect(taskService.createTask).toHaveBeenCalled());
      const payload = taskService.createTask.mock.calls[0][0];
      expect(payload.description_format).toBe('html');
      expect(payload.description).toBe('<h2>Task Goal</h2><p>Rack it.</p>');
    });

  it('raises subtasks with the task, each given to one of its assignees',
    async () => {
      taskService.createTask.mockResolvedValue({ id: 't9' });
      renderForm();
      await formReady();
      await waitFor(() =>
        expect(screen.getByText('Prashanta Acharya')).toBeInTheDocument());
      fireEvent.change(screen.getByPlaceholderText('What has to be done?'),
        { target: { value: 'Rack the new switch' } });

      fireEvent.click(screen.getByRole('button', { name: /add subtask/i }));
      fireEvent.change(screen.getByLabelText('Subtask 1 title'),
        { target: { value: 'Mount the rails' } });
      const who = screen.getByLabelText('Subtask 1 assignee');
      // Only the people on the task are offered.
      expect([...who.options].map((o) => o.textContent))
        .toEqual(['Unassigned', 'Prashanta Acharya']);
      fireEvent.change(who, { target: { value: ME.id } });
      fireEvent.change(screen.getByLabelText('Subtask 1 due date'),
        { target: { value: '2026-10-20' } });
      // A second, untitled row is an unfinished thought and must not travel.
      fireEvent.click(screen.getByRole('button', { name: /add subtask/i }));

      fireEvent.click(screen.getByRole('button', { name: /create/i }));
      await waitFor(() => expect(taskService.createTask).toHaveBeenCalled());
      const payload = taskService.createTask.mock.calls[0][0];
      expect(payload.subtasks).toEqual([
        { title: 'Mount the rails', assignee: ME.id, due_date: '2026-10-20' },
      ]);
    });

  it('clears the draft once the task is created', async () => {
    taskService.createTask.mockResolvedValue({ id: 't9', task_number: 'NIFN-TSK-2083-0009' });
    renderForm();
    await formReady();
    await waitFor(() =>
      expect(screen.getByText('Prashanta Acharya')).toBeInTheDocument());
    fireEvent.change(screen.getByPlaceholderText('What has to be done?'),
      { target: { value: 'Rack the new switch' } });
    fireEvent.click(screen.getByRole('button', { name: /create/i }));
    await waitFor(() => expect(draftService.discardDraft)
      .toHaveBeenCalledWith('task', 'new', { submitted: true }));
  });

  it('refuses to submit when the reviewer is also doing the work', async () => {
    // The server refuses this by name; the form says so first, and disables the
    // button rather than letting somebody discover it on a round trip.
    taskService.getFilterOptions.mockResolvedValue(options({ default_reviewer: null }));
    renderForm();
    await formReady();
    await waitFor(() =>
      expect(screen.getByText('Prashanta Acharya')).toBeInTheDocument());

    // Name the assignee as the reviewer as well.
    await pickFromResults('Prashanta Acharya', 'reviewer');

    const warning = await screen.findByRole('alert');
    // The wording the phase specifies, said before the request.
    expect(warning.textContent).toBe('Reviewer cannot be the same as the assignee.');
    expect(screen.getByRole('button', { name: /create/i })).toBeDisabled();
    expect(taskService.createTask).not.toHaveBeenCalled();
  });
});

/* -------------------------------------------------------------------------- */
describe('editing a task somebody else has changed', () => {
  const EDIT_CAPS = { can_edit: true, can_edit_all_fields: true };
  const loaded = (overrides = {}) => ({
    id: 't1', task_number: 'NIFN-TSK-2083-0001', title: 'Rack the new switch',
    description: 'Mount it.\n\nThen cable it.', description_format: 'text',
    priority: 'medium', due_date: '2026-10-01', updated_at: '2026-09-01T08:00:00Z',
    assignees: [{ id: 'a1', user: { id: 'worker-1' }, user_name: 'Kiran Maharjan' }],
    reviewer: { id: 'head-1', full_name: 'Bikash Kadayat' }, reviewer_name: 'Bikash Kadayat',
    checklist: [], capabilities: EDIT_CAPS, created_by_name: 'Prashanta Acharya',
    ...overrides,
  });
  const renderEdit = () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/tasks/t1/edit']}>
          <Routes><Route path="/tasks/:id/edit" element={<TaskForm />} /></Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  };

  beforeEach(() => {
    vi.clearAllMocks();
    useAuth.mockReturnValue({ user: { id: 'u1' }, role: 'maker' });
    taskService.searchEmployees.mockResolvedValue([]);
    taskService.getFilterOptions.mockResolvedValue(options());
  });

  it('converts a plain-text description to paragraphs and saves it as HTML, '
    + 'with the stamp it was loaded against', async () => {
    taskService.getTask.mockResolvedValue(loaded());
    taskService.updateTask.mockResolvedValue({ id: 't1', task_number: 'NIFN-TSK-2083-0001' });
    renderEdit();
    const editor = await screen.findByLabelText('Task description');
    expect(editor.value).toBe('<p>Mount it.</p><p>Then cable it.</p>');
    // Subtasks are not edited here.
    expect(screen.getByText(/Subtasks are managed on the task page/)).toBeInTheDocument();
    expect(screen.queryByLabelText('Subtask 1 title')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));
    await waitFor(() => expect(taskService.updateTask).toHaveBeenCalled());
    const [, payload] = taskService.updateTask.mock.calls[0];
    expect(payload.description_format).toBe('html');
    expect(payload.description).toBe('<p>Mount it.</p><p>Then cable it.</p>');
    expect(payload.expected_updated_at).toBe('2026-09-01T08:00:00Z');
    expect(payload).not.toHaveProperty('subtasks');
  });

  it('shows the conflict banner on a 409 and reloads the server values', async () => {
    taskService.getTask.mockResolvedValue(loaded());
    const theirs = loaded({ title: 'Rack the new core switch', priority: 'high',
      priority_label: 'High', updated_at: '2026-09-01T09:00:00Z' });
    taskService.updateTask.mockRejectedValue({
      response: { status: 409, data: { detail: 'The task changed.', task: theirs } },
    });
    renderEdit();
    const title = await screen.findByPlaceholderText('What has to be done?');
    fireEvent.change(title, { target: { value: 'Rack the switch today' } });
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    const banner = await screen.findByRole('alert');
    expect(banner.textContent).toContain('Task updated by another user');
    expect(screen.getByRole('button', { name: /save changes/i })).toBeDisabled();

    // Review: only the fields that differ, yours against theirs.
    fireEvent.click(screen.getByRole('button', { name: /review changes/i }));
    const diff = within(banner.querySelector('.task-conflict-diff'));
    expect(diff.getByText('Title')).toBeInTheDocument();
    expect(diff.getByText('Priority')).toBeInTheDocument();
    expect(diff.queryByText('Due date')).toBeNull();
    expect(diff.queryByText('Reviewer')).toBeNull();
    expect(diff.getByText(/Rack the new core switch/)).toBeInTheDocument();

    // Reload: the server's values replace the form, and saving is open again.
    fireEvent.click(screen.getByRole('button', { name: /^reload$/i }));
    expect(screen.getByPlaceholderText('What has to be done?'))
      .toHaveValue('Rack the new core switch');
    expect(screen.queryByRole('alert')).toBeNull();
    taskService.updateTask.mockResolvedValue({ id: 't1', task_number: 'NIFN-TSK-2083-0001' });
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));
    await waitFor(() => expect(taskService.updateTask).toHaveBeenCalledTimes(2));
    expect(taskService.updateTask.mock.calls[1][1].expected_updated_at)
      .toBe('2026-09-01T09:00:00Z');
  });
});

const CAPS = {
  can_view: true, can_edit: false, can_delete: false, can_manage_assignees: false,
  can_assign: false, can_accept: false, can_request_clarification: false,
  can_update_progress: true, can_submit_for_review: true, can_review: false,
  can_close: false, can_block: false, can_unblock: false, can_cancel: false,
  can_comment: true, can_upload: false, can_manage_checklist: false,
  can_tick_checklist: false, can_manage_dependencies: false,
};

const task = (overrides = {}) => ({
  id: 't1', task_number: 'NIFN-TSK-2083-0001', title: 'Rack the new switch',
  description: '', summary: '', status: 'assigned', status_label: 'Assigned',
  priority: 'medium', priority_label: 'Medium', due_date: null, is_overdue: false,
  progress_percent: 0, checklist_percent: null, department_name: 'ICT',
  created_by_name: 'Kiran Maharjan', reviewer_name: 'Bikash Kadayat',
  owner_name: 'Kiran Maharjan', assignee_names: ['Kiran Maharjan'],
  checklist_done: 0, checklist_total: 0, blocked_reason: '', clarification_note: '',
  assignees: [], checklist: [], checklist_groups: [], attachments: [], evidence: [],
  removed_attachments: [], comments: [], timeline: [], progress_is_auto: true,
  template_name: null, created_at: '2026-09-01T08:00:00Z', closed_at: null,
  dependencies: [], dependents: [],
  milestones: [{ key: 'created', label: 'Created', at: '2026-09-01T08:00:00Z' },
    { key: 'started', label: 'Started', at: null },
    { key: 'reviewed', label: 'Reviewed', at: null, submitted_at: null },
    { key: 'completed', label: 'Completed', at: null }],
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

const detailReady = () => waitFor(() =>
  expect(screen.getByText('Task Information')).toBeInTheDocument());

describe('the task page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuth.mockReturnValue({ user: { id: 'u1' }, role: 'maker' });
    taskService.searchEmployees.mockResolvedValue([]);
    taskService.getTemplates.mockResolvedValue([]);
    taskService.getTasks.mockResolvedValue({ results: [] });
  });

  it('shows the five stages the phase specifies', async () => {
    taskService.getTask.mockResolvedValue(task());
    renderDetail();
    await detailReady();
    const ladder = screen.getByLabelText('Workflow progress');
    expect([...ladder.querySelectorAll('li')].map((n) => n.textContent.trim()))
      .toEqual(['Created', 'In Progress', 'Submitted', 'Approved', 'Completed']);
  });

  it('reads an assigned task as Created, not as a stage of its own', async () => {
    taskService.getTask.mockResolvedValue(task({ status: 'assigned' }));
    renderDetail();
    await detailReady();
    const current = screen.getByLabelText('Workflow progress')
      .querySelector('[aria-current="step"]');
    expect(current.textContent.trim()).toBe('Created');
  });

  it('offers Submit For Review straight from Created, with no Accept step first',
    async () => {
      taskService.getTask.mockResolvedValue(task());
      renderDetail();
      await detailReady();
      expect(screen.getByRole('button', { name: /submit for review/i }))
        .toBeInTheDocument();
      // Mark Done went with personal tasks.
      expect(screen.queryByRole('button', { name: /mark done/i })).toBeNull();
    });
});

describe('the board card', () => {
  it('no longer carries a task kind', () => {
    render(<MemoryRouter><TaskCard task={{
      id: 't1', task_number: 'NIFN-TSK-2083-0001', title: 'Tidy the register',
      summary: 'Serials checked.', status: 'in_progress', status_label: 'In Progress',
      priority: 'high', priority_label: 'High', due_date: '2026-10-01',
      is_overdue: false, progress_percent: 40, department_name: 'ICT',
      owner_name: 'Kiran Maharjan', assignee_names: ['Kiran Maharjan'],
      checklist_done: 1, checklist_total: 3, comment_count: 2,
      attachment_count: 0, evidence_count: 1, reviewer_name: 'Bikash Kadayat',
    }} /></MemoryRouter>);
    expect(screen.getByText('Tidy the register')).toBeInTheDocument();
    for (const gone of [/Personal Task/, /Department Task/, /Assigned Task/]) {
      expect(screen.queryByText(gone)).toBeNull();
    }
  });
});
