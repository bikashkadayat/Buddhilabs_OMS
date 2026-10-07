/**
 * Work Queue aggregation (Phase 203 / blueprint §04).
 *
 * The union of every "waiting on me" list in the system, assembled CLIENT-SIDE
 * from endpoints that already exist. No new endpoint, no new serializer, no new
 * permission rule.
 *
 * Three rules this module exists to keep:
 *
 *  1. THE SERVER DECIDES. Every source is a scoped endpoint the module pages
 *     already call. Nothing here filters for permission, re-checks a role, or
 *     hides a row it thinks the user should not see - it renders exactly what
 *     came back. A change to `_apply_scope` therefore propagates automatically
 *     and the queue can never disagree with the module page it links to.
 *
 *  2. A FAILING SOURCE DEGRADES. Sources are fetched with allSettled, so one
 *     rejected request costs its own rows and nothing else. This mirrors the
 *     posture the sidebar badges already take: a count that will not load must
 *     never break navigation.
 *
 *  3. ACTING HERE IS ACTING THERE. Every action calls the same service method
 *     the module page calls, so audit rows, notifications and workflow
 *     transitions are byte-identical. Anything needing more than a decision and
 *     a remark carries no inline action and opens the module page instead.
 */
import { taskService } from './taskService';
import { appraisalService } from './appraisalService';
import { TOTAL_STAGES } from '../components/appraisal/appraisalLabels';
import { memoService } from './memoService';
import { minuteService } from './minuteService';
import { circularService } from './circularService';
import { leaveService } from './leaveService';
import { inventoryService, assetLifecycle } from './inventoryService';
import { workforceService } from './workforceService';

/** Record types, used for the tag colour and the type filter. */
export const TYPES = {
  TASK: 'task',
  MEMO: 'memo',
  MINUTE: 'minute',
  CIRCULAR: 'circular',
  LEAVE: 'leave',
  ASSET: 'asset',
  ATTENDANCE: 'attendance',
  APPRAISAL: 'appraisal',
};

/** What the user is being asked to do. Drives the filter chips. */
/**
 * The inline actions for a memo waiting on the viewer
 * (Phase MEMO-ACT-ENDPOINT-400-ROOT-CAUSE).
 *
 * ROOT CAUSE OF "Request failed with status code 400" in the queue: this used to
 * send `{decision: "approve", remarks: ""}` for every memo. The endpoint accepts
 * only "proceed" or "reject", so the request died on the decision field - and
 * behind that failure sat a second one, because every memo step (Review,
 * Recommend, Support, Approve) requires a comment of at least 10 characters.
 * Captured in the browser, verbatim:
 *
 *   request   {"decision":"approve","remarks":""}
 *   400       {"decision":["\"approve\" is not a valid choice."]}
 *
 * Everything below is read from `row.my_step`, which the server fills from the
 * same constants it enforces: the verb for the viewer's role, whether a comment
 * is required, and how long it must be. Nothing here is a guess about the rule.
 *
 * No step for the viewer (the memo moved on since the list loaded) means no
 * inline action - the row offers "Open" instead of a button the server would
 * refuse.
 */
const MEMO_VERB = {
  reviewer: 'review', recommender: 'recommend', supporter: 'support', approver: 'approve',
};

/**
 * Where the viewer's step sits, from `my_step` (Phase MEMO-QUEUE-UX-HARDENING).
 * Null when the row carries no step - an older server, or the memo moved on.
 */
export const memoWorkflow = (row) => {
  const step = row.my_step;
  if (!step) return null;
  return {
    roleLabel: step.role_label,
    position: step.position || step.sequence || 1,
    total: step.total_steps || step.position || 1,
    next: step.next_step
      ? { roleLabel: step.next_step.role_label, name: step.next_step.assignee_name }
      : null,
    isFinalStep: Boolean(step.is_final_step),
    authorName: step.author_name || '',
  };
};

/** "Reviewer · Step 1 of 4 · Next: Recommender" - the card's one-line summary. */
const memoStepLine = (flow) => {
  if (!flow) return '';
  const next = flow.isFinalStep ? 'Final approval' : flow.next ? `Next: ${flow.next.roleLabel}` : '';
  return [flow.roleLabel, `Step ${flow.position} of ${flow.total}`, next].filter(Boolean).join(' · ');
};

/**
 * Confirm, the moment a dialog opens, that the memo is still waiting on this
 * person. The queue may have loaded minutes ago; the author may have withdrawn
 * the memo, or someone may already have acted. Without this, the first place
 * the person learns it is the server's refusal - after writing their remark.
 *
 * Returns {ok:false, reason} with a sentence, or {ok:true}. A failed READ is
 * not a refusal (useRowAction lets the person continue, and the server decides).
 */
const confirmStillMine = (row) => async () => {
  const memo = await memoService.getMemo(row.id);
  const mine = memo?.my_step;
  if (memo?.is_read_only) {
    return { ok: false,
      reason: `This memo is ${String(memo.status_label || memo.status).toLowerCase()} and can no longer be actioned.` };
  }
  if (!mine || !mine.is_my_turn || (row.my_step?.id && mine.id !== row.my_step.id)) {
    const withWhom = memo?.pending_with?.name;
    return { ok: false,
      reason: withWhom
        ? `This memo has moved on — it is now with ${withWhom}${memo.pending_with.role_label ? ` (${memo.pending_with.role_label})` : ''}.`
        : 'This memo is no longer waiting on you.' };
  }
  return { ok: true };
};

const memoActions = (row) => {
  const step = row.my_step;
  if (!step) return [];
  const min = step.min_comment_length || 10;
  const workflow = memoWorkflow(row);
  const precheck = confirmStillMine(row);
  return [
    {
      label: step.action_label,
      verb: MEMO_VERB[step.role_type] || 'approve',
      variant: 'primary',
      needsRemark: Boolean(step.requires_comment),
      minRemark: step.requires_comment ? min : 0,
      workflow,
      precheck,
      dialogTitle: `${step.action_label} memo`,
      remarkPlaceholder: 'What you checked, and anything the next person should know.',
      run: (remarks) => memoService.actOnMemo(row.id, { decision: 'proceed', remarks }),
    },
    {
      label: 'Reject',
      verb: 'reject',
      variant: 'secondary',
      needsRemark: true,
      minRemark: min,
      workflow,
      precheck,
      dialogTitle: 'Reject memo',
      remarkPlaceholder: 'So the author knows what to change.',
      run: (remarks) => memoService.actOnMemo(row.id, { decision: 'reject', remarks }),
    },
  ];
};

export const KINDS = {
  APPROVAL: 'approval',
  REVIEW: 'review',
  ACKNOWLEDGE: 'acknowledge',
  ACTION: 'action',
};

const DAY_MS = 86_400_000;

/** Initials for the requester avatar. Never throws on a missing name. */
const initialsOf = (name) => {
  const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return '—';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
};

/**
 * First present value among several candidate keys.
 *
 * Every module names its fields differently and some of them are nested. Rather
 * than one mapper per shape with its own optional chaining, sources declare the
 * keys they might use and this walks them - so an unexpected payload yields a
 * blank string rather than a TypeError that blanks the whole queue.
 */
const pick = (row, keys, fallback = '') => {
  for (const key of keys) {
    const value = key.split('.').reduce((acc, k) => (acc == null ? acc : acc[k]), row);
    if (value !== undefined && value !== null && value !== '') return value;
  }
  return fallback;
};

/** DRF pagination envelope, a bare array, or a service that already unwrapped. */
const rows = (payload) => {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.results)) return payload.results;
  if (Array.isArray(payload?.items)) return payload.items;
  if (Array.isArray(payload?.data)) return payload.data;
  return [];
};

/**
 * Sort weight. Lower sorts first.
 *
 * Overdue always wins; within a bucket the oldest goes first, because the item
 * that has waited longest is the one most likely to be blocking somebody. Type
 * is deliberately NOT part of the ordering - a memo is not inherently more
 * urgent than a leave request, and encoding that here would quietly impose a
 * governance opinion the organisation never agreed to.
 */
export const priority = (item, now = Date.now()) => {
  const due = item?.dueAt ? new Date(item.dueAt).getTime() : null;
  if (due !== null && !Number.isNaN(due)) {
    if (due < now) return 0;                       // overdue
    if (due - now < DAY_MS) return 1;              // due today
  }
  const created = item?.createdAt ? new Date(item.createdAt).getTime() : null;
  if (created !== null && !Number.isNaN(created) && now - created > 2 * DAY_MS) {
    return 2;                                      // aging
  }
  return 3;                                        // normal
};

/** Stable comparator: bucket, then oldest first, then id so order never jitters. */
export const compareItems = (a, b, now = Date.now()) => {
  const pa = priority(a, now);
  const pb = priority(b, now);
  if (pa !== pb) return pa - pb;
  const ca = a?.createdAt ? new Date(a.createdAt).getTime() : Infinity;
  const cb = b?.createdAt ? new Date(b.createdAt).getTime() : Infinity;
  if (ca !== cb) return ca - cb;
  return String(a?.id).localeCompare(String(b?.id));
};

export const isOverdue = (item, now = Date.now()) => priority(item, now) === 0;

/** "2 days" / "4 hours" / "overdue 1 day" - the waiting column. */
export const waitingLabel = (item, now = Date.now()) => {
  if (item?.dueAt) {
    const due = new Date(item.dueAt).getTime();
    if (!Number.isNaN(due)) {
      if (due < now) {
        const days = Math.max(1, Math.round((now - due) / DAY_MS));
        return `overdue ${days} day${days === 1 ? '' : 's'}`;
      }
      if (due - now < DAY_MS) return 'due today';
    }
  }
  const created = item?.createdAt ? new Date(item.createdAt).getTime() : null;
  if (created === null || Number.isNaN(created)) return '—';
  const ms = Math.max(0, now - created);
  if (ms < 3_600_000) return `${Math.max(1, Math.round(ms / 60_000))} min`;
  if (ms < DAY_MS) {
    const h = Math.round(ms / 3_600_000);
    return `${h} hour${h === 1 ? '' : 's'}`;
  }
  const d = Math.round(ms / DAY_MS);
  return `${d} day${d === 1 ? '' : 's'}`;
};

// ---------------------------------------------------------------------------
// Sources
//
// One entry per "waiting on me" list. `fetch` calls the SAME service method the
// module page calls; `map` turns one row into a QueueItem. Adding a source is an
// entry here and nothing else - appraisal (Phase APM-03b) was exactly that.
// ---------------------------------------------------------------------------

/**
 * Which of the six situations a task row is in.
 *
 * Derived from the row rather than fetched as six separate scopes — see the
 * task source below. Order matters: a task can be several of these at once
 * (overdue AND blocked), and the label should name the one that decides what
 * the person does next.
 */
export const taskSituation = (row) => {
  if (row.status === 'blocked') return 'blocked';
  if (row.status === 'under_review') return 'under review';
  if (row.is_overdue) return `${row.overdue_days || ''} day(s) overdue`.trim();
  if (row.status === 'assigned') return 'awaiting your acceptance';
  // "Returned" is not a status of its own — rework puts a task back to In
  // Progress — so it is inferred from progress having been made already.
  if (row.status === 'in_progress' && row.progress_percent > 0) return 'in progress';
  return row.status_label || 'assigned';
};

/** The queue's own filter chips. A task is an ACTION unless it wants a decision. */
export const taskKind = (row) => (
  row.status === 'under_review' ? KINDS.REVIEW : KINDS.ACTION
);

/**
 * Inline actions, matching what the SERVER says the caller may do.
 *
 * Only actions needing no typing appear here. Reviewing a task means approving
 * or returning it, and returning it requires a written reason the API enforces —
 * a queue row has nowhere to type one, so review opens the task instead. That is
 * the same escalation rule memos and minutes already follow.
 */
export const taskActions = (row) => {
  if (row.status === 'assigned') {
    return [{
      label: 'Accept', verb: 'accept', variant: 'primary',
      run: () => taskService.accept(row.id),
    }];
  }
  return [];
};

/**
 * What an appraisal is waiting for, in the words the specification uses.
 *
 * Derived from the STATUS the server returned rather than from the caller's
 * role: `needs_me` has already decided that this row is this person's turn, so
 * the stage alone names the action. Re-deriving "am I the supervisor here"
 * client-side would be a second, weaker copy of the rule that put the row in
 * the list.
 */
export const appraisalSituation = (row) => ({
  goal_setting: 'Objectives to submit',
  goal_approval: 'Goal approval',
  mid_year_review: 'Mid-year review',
  self_assessment: 'Self assessment',
  supervisor_review: 'Review pending',
  review_committee: 'Committee review',
  final_review: 'Final review',
  development_plan: 'Development plan approval',
  training_plan: 'Training plan approval',
}[row.status] || row.status_label || 'Appraisal');

export const SOURCES = [
  {
    // Phase T4.1. Tasks are FIRST because they are the only source here that is
    // the person's own work rather than a decision about somebody else's — and
    // the queue is read top-down.
    //
    // ONE request, not six. The specification names six task situations
    // (assigned, under review, due today, overdue, returned, blocked) and it
    // would have been easy to make each a source. They are all slices of
    // `?scope=needs_me`, which the server already computes; six requests would
    // have meant six chances for the queue to show the same task twice under
    // different labels, and six times the load for one person's list. The KIND
    // is derived per row below instead.
    type: TYPES.TASK,
    label: 'Tasks',
    fetch: () => taskService.getTasks({ scope: 'needs_me' }),
    map: (row) => ({
      id: `task:${row.id}`,
      sourceId: row.id,
      type: TYPES.TASK,
      kind: taskKind(row),
      title: pick(row, ['title'], 'Untitled task'),
      subtitle: [
        pick(row, ['task_number']),
        taskSituation(row),
        row.checklist_total
          ? `${row.checklist_done}/${row.checklist_total} checklist` : '',
      ].filter(Boolean).join(' · '),
      requester: pick(row, ['created_by_name'], 'Unknown'),
      createdAt: pick(row, ['created_at'], null),
      dueAt: pick(row, ['due_date'], null),
      href: `/tasks/${row.id}`,
      actions: taskActions(row),
    }),
  },
  {
    type: TYPES.MEMO,
    label: 'Memos',
    fetch: () => memoService.listMemos({ scope: 'pending' }),
    map: (row) => ({
      id: `memo:${row.id}`,
      sourceId: row.id,
      type: TYPES.MEMO,
      // Only an Approver's step is an approval. A Reviewer, Recommender or
      // Supporter step is filed under Reviews - it used to count as an
      // "Approval" whatever the role, which is how a review step came to wear an
      // "Approve" button.
      kind: row.my_step && row.my_step.role_type !== 'approver' ? KINDS.REVIEW : KINDS.APPROVAL,
      title: pick(row, ['subject'], 'Untitled memo'),
      subtitle: [pick(row, ['memo_number']), pick(row, ['created_by_name', 'created_by.full_name'])]
        .filter(Boolean).join(' · '),
      requester: pick(row, ['created_by_name', 'created_by.full_name', 'from_name'], 'Unknown'),
      createdAt: pick(row, ['submitted_at', 'created_at'], null),
      dueAt: pick(row, ['due_date', 'deadline'], null),
      href: `/memos/${row.id}`,
      stepLine: memoStepLine(memoWorkflow(row)),
      actions: memoActions(row),
    }),
  },
  {
    type: TYPES.MINUTE,
    label: 'Minutes',
    fetch: () => minuteService.getMinutes({ scope: 'needs_me' }),
    map: (row) => ({
      id: `minute:${row.id}`,
      sourceId: row.id,
      type: TYPES.MINUTE,
      kind: KINDS.ACTION,
      title: pick(row, ['subject'], 'Untitled minute'),
      subtitle: [pick(row, ['minute_number']), pick(row, ['type_label', 'minute_type'])]
        .filter(Boolean).join(' · '),
      requester: pick(row, ['created_by_name', 'fro_name', 'created_by.full_name'], 'Unknown'),
      createdAt: pick(row, ['created_at'], null),
      dueAt: pick(row, ['due_date'], null),
      href: `/minutes/${row.id}`,
      // No inline action. A minute needs either a review decision with context or
      // an acknowledgement whose meaning depends on the agenda - both belong on
      // the record, not on a queue row.
      actions: [],
    }),
  },
  {
    type: TYPES.CIRCULAR,
    label: 'Circulars',
    fetch: () => circularService.getCirculars({ scope: 'my_acknowledgements' }),
    map: (row) => ({
      id: `circular:${row.id}`,
      sourceId: row.id,
      type: TYPES.CIRCULAR,
      kind: KINDS.ACKNOWLEDGE,
      title: pick(row, ['subject'], 'Untitled circular'),
      subtitle: [pick(row, ['circular_number']), 'acknowledgement required']
        .filter(Boolean).join(' · '),
      requester: pick(row, ['created_by_name', 'issued_by_name'], 'Administration'),
      // `broadcast_at` is the real serializer field; the past-tense spelling was a
      // guess this fixture disproved. Both are kept - the register endpoint uses
      // the longer form - and the chain falls through to issue/creation anyway.
      createdAt: pick(row, ['broadcast_at', 'broadcasted_at', 'issued_at', 'created_at'], null),
      dueAt: pick(row, ['acknowledgement_deadline', 'due_date'], null),
      href: `/circulars/${row.id}`,
      actions: [
        { label: 'Acknowledge', verb: 'acknowledge', variant: 'primary',
          run: () => circularService.acknowledge(row.id, {}) },
      ],
    }),
  },
  {
    type: TYPES.LEAVE,
    label: 'Leave',
    // Already mapped by leaveService.mapLeave, hence the flatter field names.
    fetch: () => leaveService.getPendingApprovals(),
    map: (row) => ({
      id: `leave:${row.id}`,
      sourceId: row.id,
      type: TYPES.LEAVE,
      kind: KINDS.APPROVAL,
      title: `${pick(row, ['type'], 'Leave')} — ${pick(row, ['employee'], 'Unknown')}`,
      subtitle: [
        [pick(row, ['start']), pick(row, ['end'])].filter(Boolean).join(' to '),
        row.days ? `${row.days} day${row.days === 1 ? '' : 's'}` : '',
      ].filter(Boolean).join(' · '),
      requester: pick(row, ['employee'], 'Unknown'),
      createdAt: pick(row, ['applied', 'created_at'], null),
      dueAt: pick(row, ['start'], null),
      href: '/leave/pending',
      actions: [
        { label: 'Approve', verb: 'approve', variant: 'primary',
          run: () => leaveService.reviewLeave(row.id, row.status, 'approved') },
        { label: 'Reject', verb: 'reject', variant: 'secondary', needsRemark: true,
          run: (remarks) => leaveService.reviewLeave(row.id, row.status, 'rejected', remarks) },
      ],
    }),
  },
  {
    type: TYPES.ASSET,
    label: 'Asset take-outs',
    fetch: () => inventoryService.takeouts({ status: 'pending' }),
    map: (row) => ({
      id: `takeout:${row.id}`,
      sourceId: row.id,
      type: TYPES.ASSET,
      kind: KINDS.APPROVAL,
      title: `Take-out — ${pick(row, ['item_name', 'item.name', 'asset_name'], 'asset')}`,
      subtitle: [pick(row, ['purpose']), pick(row, ['destination'])]
        .filter(Boolean).join(' · '),
      requester: pick(row, ['requested_by_name', 'employee_name', 'requested_by.full_name'], 'Unknown'),
      createdAt: pick(row, ['created_at', 'requested_at'], null),
      dueAt: pick(row, ['expected_return_date'], null),
      href: '/inventory/approvals',
      actions: [
        { label: 'Approve', verb: 'approve', variant: 'primary',
          run: () => inventoryService.approveTakeout(row.id) },
        { label: 'Reject', verb: 'reject', variant: 'secondary', needsRemark: true,
          run: (remarks) => inventoryService.rejectTakeout(row.id, remarks) },
      ],
    }),
  },
  {
    type: TYPES.ASSET,
    key: 'asset-requests',
    label: 'Asset requests',
    // `requests` lives on assetLifecycle, not inventoryService — the QA
    // harness caught this; unit tests never call fetch(), so it was invisible.
    fetch: () => assetLifecycle.requests({ status: 'pending' }),
    map: (row) => ({
      id: `request:${row.id}`,
      sourceId: row.id,
      type: TYPES.ASSET,
      kind: KINDS.REVIEW,
      title: `Asset request — ${pick(row, ['item_name', 'category_name', 'description'], 'item')}`,
      subtitle: pick(row, ['justification', 'purpose'], 'Awaiting your decision'),
      requester: pick(row, ['requested_by_name', 'employee_name'], 'Unknown'),
      createdAt: pick(row, ['created_at'], null),
      dueAt: pick(row, ['needed_by'], null),
      href: '/inventory/requests',
      // Deliberately no inline action: an asset request decision routes through
      // either the supervisor or the inventory stage depending on where it sits,
      // and guessing which from a list row is exactly the kind of half-informed
      // approval the queue must not enable.
      actions: [],
    }),
  },
  {
    type: TYPES.ATTENDANCE,
    label: 'Attendance corrections',
    fetch: () => workforceService.corrections({ status: 'pending' }),
    map: (row) => ({
      id: `correction:${row.id}`,
      sourceId: row.id,
      type: TYPES.ATTENDANCE,
      kind: KINDS.REVIEW,
      title: `Punch correction — ${pick(row, ['employee_name', 'employee.full_name'], 'Unknown')}`,
      subtitle: [pick(row, ['attendance_date']), pick(row, ['reason'])]
        .filter(Boolean).join(' · '),
      requester: pick(row, ['employee_name', 'employee.full_name'], 'Unknown'),
      createdAt: pick(row, ['created_at', 'submitted_at'], null),
      dueAt: null,
      href: '/workforce/corrections',
      actions: [
        { label: 'Approve', verb: 'approve', variant: 'primary',
          run: () => workforceService.approveCorrection(row.id) },
        { label: 'Reject', verb: 'reject', variant: 'secondary', needsRemark: true,
          run: (remarks) => workforceService.rejectCorrection(row.id, remarks) },
      ],
    }),
  },
  {
    // Phase APM-03b. ONE request for every appraisal stage waiting on this
    // person, whatever hat they are wearing: the server's `needs_me` scope
    // pairs each stage with whoever's turn it is - the employee at self
    // assessment, the supervisor at goal setting, mid-year, review, final,
    // development and training, the committee at theirs. Five separate sources
    // for the five situations the specification names would have been five
    // chances to show the same appraisal twice under different labels.
    //
    // NO INLINE ACTIONS, deliberately, and this is the one source where that is
    // a rule rather than a shortcut. Every appraisal step means reading
    // somebody's written year and adding to it; a one-click "approve" on a
    // queue row is exactly the rubber-stamp this module exists to prevent. The
    // row opens the record.
    type: TYPES.APPRAISAL,
    label: 'Appraisals',
    fetch: () => appraisalService.getAppraisals({ scope: 'needs_me' }),
    map: (row) => ({
      id: `appraisal:${row.id}`,
      sourceId: row.id,
      type: TYPES.APPRAISAL,
      kind: KINDS.REVIEW,
      title: `${row.employee_name} — ${appraisalSituation(row)}`,
      subtitle: [pick(row, ['cycle_name']),
        `stage ${row.stage_index} of ${TOTAL_STAGES}`]
        .filter(Boolean).join(' \u00b7 '),
      requester: pick(row, ['employee_name'], 'Unknown'),
      createdAt: pick(row, ['updated_at', 'created_at'], null),
      dueAt: null,
      href: `/appraisals/${row.id}`,
      actions: [],
    }),
  },
];

/** Stable key for a source (two of them share `type: 'asset'`). */
export const sourceKey = (source) => source.key || source.type;

/**
 * Map one source's payload into QueueItems.
 *
 * A row that throws is dropped rather than taking the source down with it: a
 * single malformed record must not empty a queue the user relies on.
 */
export const normaliseSource = (source, payload) => {
  const out = [];
  for (const row of rows(payload)) {
    try {
      const item = source.map(row);
      if (item && item.id) {
        out.push({ ...item, requesterInitials: initialsOf(item.requester) });
      }
    } catch {
      // Deliberately swallowed - see the docstring above.
    }
  }
  return out;
};

/**
 * Start one source, converting a SYNCHRONOUS throw into a rejected promise.
 *
 * `Promise.allSettled(SOURCES.map((s) => s.fetch()))` looks like it tolerates
 * failure and does not: if a fetch throws before returning a promise, `.map()`
 * throws, allSettled is never reached, and the whole queue dies for what should
 * have cost one source's rows. The QA harness found this - its API stub throws
 * synchronously on an unrouted URL, and the entire page rendered its error
 * state - but the same hole is open in production for any service that
 * validates a parameter before dispatching.
 */
const start = (source) => {
  try {
    return Promise.resolve(source.fetch());
  } catch (error) {
    return Promise.reject(error);
  }
};

/** Fetch every source, tolerate failures, return items plus per-source health. */
export const fetchWorkQueue = async () => {
  const settled = await Promise.allSettled(SOURCES.map(start));
  const items = [];
  const sources = settled.map((result, i) => {
    const source = SOURCES[i];
    if (result.status === 'fulfilled') {
      items.push(...normaliseSource(source, result.value));
      return { key: sourceKey(source), label: source.label, ok: true, error: null };
    }
    return {
      key: sourceKey(source),
      label: source.label,
      ok: false,
      error: result.reason?.message || 'Request failed',
    };
  });

  const now = Date.now();
  items.sort((a, b) => compareItems(a, b, now));
  return { items, sources };
};

/** Chip counts. Derived, never stored, so they can never disagree with the list. */
export const summarise = (items, now = Date.now()) => ({
  total: items.length,
  overdue: items.filter((i) => isOverdue(i, now)).length,
  approval: items.filter((i) => i.kind === KINDS.APPROVAL).length,
  review: items.filter((i) => i.kind === KINDS.REVIEW).length,
  acknowledge: items.filter((i) => i.kind === KINDS.ACKNOWLEDGE).length,
  action: items.filter((i) => i.kind === KINDS.ACTION).length,
});

/** Apply a filter chip. Unknown filter names fall through to "all". */
export const applyFilter = (items, filter, now = Date.now()) => {
  switch (filter) {
    case 'approvals': return items.filter((i) => i.kind === KINDS.APPROVAL);
    case 'reviews': return items.filter((i) => i.kind === KINDS.REVIEW);
    case 'acknowledge': return items.filter((i) => i.kind === KINDS.ACKNOWLEDGE);
    case 'overdue': return items.filter((i) => isOverdue(i, now));
    default: return items;
  }
};

/**
 * Filter chips. Lives here rather than beside the component because the page,
 * the component and the URL validator all need it, and a constant exported from
 * a component module breaks React Fast Refresh.
 */
export const FILTERS = [
  { key: 'all', label: 'All', countKey: 'total' },
  { key: 'approvals', label: 'Approvals', countKey: 'approval' },
  { key: 'reviews', label: 'Reviews', countKey: 'review' },
  { key: 'acknowledge', label: 'Acknowledge', countKey: 'acknowledge' },
  { key: 'overdue', label: 'Overdue', countKey: 'overdue', tone: 'danger' },
];
