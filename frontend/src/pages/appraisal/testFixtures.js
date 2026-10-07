/**
 * Shared fixtures for the appraisal UX tests (Phase APM-03b).
 *
 * Built to be ADVERSARIAL where it matters. The evidence and team fixtures put
 * the person with the worst figures FIRST alphabetically, so any component that
 * quietly reorders by a metric produces a visibly different order than the one
 * the server sent — a fixture sorted best-first would let a ranking bug pass.
 */
export const CAPS = {
  can_view: true, can_edit_goals: false, can_agree_goals: false,
  can_record_mid_year: false, can_write_self_assessment: false,
  can_submit_self_assessment: false, can_write_supervisor_review: false,
  can_write_committee_review: false, can_finalise: false,
  can_manage_development_plan: false, can_manage_training_plan: false,
  can_decide_training: false, can_attach_evidence: false, can_return: false,
  can_reopen: false, can_delete: false, can_rate_as_employee: false,
  can_rate_as_supervisor: false, can_rate_as_committee: false,
  is_subject: false,
};

export const goal = (objective, weight, extra = {}) => ({
  id: `g-${objective}`, objective, weight, description: '',
  status: 'approved', status_label: 'Approved',
  target: 'What success looks like', achievement: '', progress_percent: 40,
  due_date: '2026-06-30', owner_name: 'Bikash Kadayat', ...extra,
});

export const appraisal = (extra = {}) => ({
  id: 'a-1',
  cycle: 'c-1', cycle_name: 'FY 2083/84 Annual',
  employee: 'u-1', employee_name: 'Bikash Kadayat',
  designation: 'Officer', department_name: 'Engineering',
  supervisor: 'u-2', supervisor_name: 'Sita Rai',
  // Positions come from the SERVER's ladder: Final Review is the 7th of 10
  // stages since Goal Approval was inserted (APM-FINAL).
  status: 'final_review', status_label: 'Final Review', stage_index: 7,
  goal_count: 2, goal_weight_total: 100,
  self_assessment: '', supervisor_comments: '', committee_comments: '',
  final_summary: '',
  promotion_readiness: '', promotion_readiness_label: '',
  promotion_rationale: '', successor_for: '',
  goals: [goal('Deliver the quarterly return', 60),
    goal('Improve the archive process', 40)],
  competency_ratings: [], development_plans: [], training_plans: [],
  evidence_references: [], timeline: [],
  committee_detail: [],
  capabilities: { ...CAPS },
  ...extra,
});

export const EVIDENCE = {
  period_start: '2025-09-01', period_end: '2026-08-31', available: true,
  sources: ['task'], low_volume: false,
  disclaimer: 'Counts and durations describing recorded activity.',
  note: 'Activity evidence produced by the task module and cited here. It is '
    + 'context for a human judgement, not a measure of performance, and nothing '
    + 'in this appraisal is computed from it.',
  headline: [
    { key: 'tasks_assigned', label: 'Tasks Assigned', unit: 'count', value: 12,
      definition: 'Tasks assigned in the period.', basis_of: null, missing: false },
    { key: 'tasks_completed', label: 'Tasks Completed', unit: 'count', value: 9,
      definition: 'Tasks completed in the period.', basis_of: null, missing: false },
    { key: 'task_completion_percent', label: 'Completion', unit: 'percent',
      value: 75, definition: 'Completed as a share of assigned.',
      basis_of: '9 of 12 tasks', missing: false },
    { key: 'on_time_percent', label: 'On Time', unit: 'percent', value: 75,
      definition: 'Completed by their due date.',
      basis_of: '6 of 8 with a due date', missing: false },
    { key: 'review_participation', label: 'Reviews', unit: 'count', value: 2,
      definition: 'Review decisions made.', basis_of: null, missing: false },
    { key: 'evidence_uploaded', label: 'Evidence Files', unit: 'count', value: 5,
      definition: 'Files attached to tasks.', basis_of: null, missing: false },
    { key: 'checklist_completion', label: 'Checklist', unit: 'percent',
      value: 80, definition: 'Checklist items completed.',
      basis_of: '24 of 30 items', missing: false },
  ],
  snapshots: [],
};

export const DASHBOARD = {
  employee: {
    current_appraisal: 'a-1', cycle: 'FY 2083/84 Annual',
    // Self Assessment is the 4th of the ten backend stages, which maps to the
    // employee's step 2 of 5 ("My Review").
    stage: 'Self Assessment', stage_index: 4, total_stages: 10,
    awaiting_me: true,
    goals: [goal('Deliver the quarterly return', 60),
      goal('Improve the archive process', 40)],
    goal_weight_total: 100, development_plan: [],
    training_recommendations: [], history: [],
    note: 'Your own appraisal record. Nothing here is scored or compared with '
      + "anybody else's.",
  },
};

export const MANAGER = {
  team_size: 2, pending_reviews: 1, completed: 0, completion_percent: 0,
  by_stage: [], development_needs: [{ area: 'Presentation skills', count: 2 }],
  // Alphabetical, and deliberately with the LEAST advanced appraisal first, so
  // an ordering by stage would visibly differ from what the server sent.
  team: [
    { appraisal_id: 'a-1', employee: 'Aarati Shrestha', stage: 'Goal Setting',
      stage_index: 1, is_closed: false, goals: 1, goal_weight_total: 60,
      awaiting_me: true },
    { appraisal_id: 'a-2', employee: 'Zenith Rai', stage: 'Review Committee',
      stage_index: 6, is_closed: false, goals: 3, goal_weight_total: 100,
      awaiting_me: false },
  ],
  note: 'Your direct reports, ordered by name.',
};

export const HR = {
  cycle_completion: { total: 10, closed: 4, percent: 40, in_progress: 6 },
  by_stage: [{ status: 'goal_setting', label: 'Goal Setting', count: 3 }],
  by_department: [
    { department: 'Engineering', total: 6, closed: 2, completion_percent: 33 },
    { department: 'Finance', total: 4, closed: 2, completion_percent: 50 },
  ],
  review_status: { with_supervisor: 2, with_committee: 1,
    awaiting_self_assessment: 3 },
  training_needs: {
    total: 4, identified: 3, approved: 1,
    by_kind: [
      { kind: 'training', label: 'Training', count: 1 },
      { kind: 'certification', label: 'Certification', count: 1 },
      { kind: 'mentorship', label: 'Mentorship', count: 1 },
      { kind: 'on_the_job', label: 'On-the-job', count: 1 },
    ],
    by_priority: [{ priority: 'high', label: 'High', count: 2 }],
    top_requests: [{ title: 'ISO 27001', count: 2 }],
  },
  promotion_by_readiness: [
    { value: 'ready', label: 'Ready', count: 1 },
    { value: 'ready_with_development', label: 'Ready With Development', count: 1 },
    { value: 'development_required', label: 'Development Required', count: 1 },
  ],
  // Alphabetical, with the LEAST favourable answer first: a readiness ordering
  // would put Zenith at the top and be immediately visible.
  promotion_readiness: [
    { employee: 'Aarati Shrestha', department: 'Engineering',
      readiness: 'development_required',
      readiness_label: 'Development Required',
      rationale: 'Needs a full cycle owning the close.',
      recommended_in: 'FY 2083/84 Annual' },
    { employee: 'Zenith Rai', department: 'Finance', readiness: 'ready',
      readiness_label: 'Ready',
      rationale: 'Operating a grade above for two cycles.',
      recommended_in: 'FY 2083/84 Annual' },
  ],
  succession: [
    { employee: 'Zenith Rai', role: 'Finance Manager', department: 'Finance' },
  ],
  note: 'Process progress across the organisation.',
};
