/**
 * Appraisal vocabulary, in one place (Phase APM-03b).
 *
 * WHY THESE ARE WORDS AND NOT NUMBERS
 * -----------------------------------
 * `CompetencyRating.level` is stored as an ordinal TextChoice on the server
 * precisely so nobody can average it. This file must not undo that: there is no
 * map from a level to a score, no rank index, and no ordering helper that would
 * let a table be sorted "best first". LEVELS is an array only because a select
 * needs an order to render its options in, and that order is the model's.
 *
 * WHY THE STAGE LADDER IS DUPLICATED AT ALL
 * -----------------------------------------
 * It is not: the detail payload carries `status_label` and `stage_index`, and
 * every screen renders those. STAGES exists for ONE job — drawing the tracker,
 * which has to show the steps a record has not reached yet and therefore cannot
 * be built from the record's own status. If the server's ladder changes, the
 * tracker shows the wrong future steps and every other screen stays correct;
 * that is the smallest blast radius available without a ladder endpoint.
 */

/** The ten stages, in the server's order (appraisal.models.Appraisal.LADDER). */
export const STAGES = [
  { value: 'goal_setting', label: 'Goal Setting' },
  { value: 'goal_approval', label: 'Goal Approval' },
  { value: 'mid_year_review', label: 'Mid-Year Review' },
  { value: 'self_assessment', label: 'Self Assessment' },
  { value: 'supervisor_review', label: 'Supervisor Review' },
  { value: 'review_committee', label: 'Review Committee' },
  { value: 'final_review', label: 'Final Review' },
  { value: 'development_plan', label: 'Growth Plan' },
  { value: 'training_plan', label: 'Training Plan' },
  { value: 'closed', label: 'Closed' },
];

/**
 * The FIVE steps an employee sees, over the ten the engine runs (Phase APM-UX).
 *
 * The backend ladder is unchanged: ten stages, ten stamps, ten audit rows, ten
 * permission gates. This is a display grouping and nothing else.
 *
 * WHY IT IS KEYED ON stage_index AND NOT ON THE STAGE NAME
 * -------------------------------------------------------
 * The employee dashboard sends `stage` as a DISPLAY LABEL ("Self Assessment"),
 * not the raw status. Grouping on that label would mean a reworded stage
 * silently regrouped somebody's appraisal — the same class of mistake as
 * matching `stage === 'Closed'` to decide whether an appraisal is finished,
 * which would have moved a closed record back into the live list with no error
 * anywhere. `stage_index` is a position, it is already sent, and the backend
 * pins the ladder to its exact ten values in a test.
 *
 * Where the raw `status` IS available — the record page — `stepForStatus` uses
 * it, because a position is only as stable as the list it indexes into.
 */
export const EMPLOYEE_STEPS = [
  { key: 'goals', label: 'My Goals',
    hint: 'Agree what you are working towards' },
  { key: 'review', label: 'My Review',
    hint: 'Your own account of the year' },
  { key: 'manager', label: 'Manager Review',
    hint: 'With your manager' },
  { key: 'feedback', label: 'Final Feedback',
    hint: 'Read what was decided' },
  { key: 'growth', label: 'My Growth Plan',
    hint: 'What comes next, and the support for it' },
];

/**
 * Which of the ten backend stages belongs to which of the five steps.
 *
 * Goal Setting, Goal Approval and Mid-Year are all "agreeing what you are
 * working towards" from the employee's side. Supervisor Review and Review
 * Committee are both "with your manager" — the committee's involvement stays
 * visible in the FEEDBACK, where its comment is attributed, but it is not a
 * step the employee has to understand to use the system.
 */
const STATUS_TO_STEP = {
  goal_setting: 'goals',
  goal_approval: 'goals',
  mid_year_review: 'goals',
  self_assessment: 'review',
  supervisor_review: 'manager',
  review_committee: 'manager',
  final_review: 'feedback',
  development_plan: 'growth',
  training_plan: 'growth',
  closed: 'growth',
};

/** 1-based ladder position -> step key. Mirrors STATUS_TO_STEP by position. */
const INDEX_TO_STEP = STAGES.map((stage) => STATUS_TO_STEP[stage.value]);

/** The step a raw backend status belongs to. Preferred where status is sent. */
export const stepForStatus = (status) =>
  EMPLOYEE_STEPS.find((s) => s.key === STATUS_TO_STEP[status]) || null;

/** The step a 1-based `stage_index` belongs to. Used on the dashboard. */
export const stepForIndex = (index) => {
  const key = INDEX_TO_STEP[(index || 1) - 1];
  return EMPLOYEE_STEPS.find((s) => s.key === key) || null;
};

/** Position of a step, 1-based, for "step 2 of 5". */
export const stepNumber = (step) =>
  EMPLOYEE_STEPS.findIndex((s) => s.key === step?.key) + 1;

/** An appraisal is finished when it is closed — not when it reaches growth. */
export const isComplete = (status, index) =>
  status === 'closed' || index === STAGES.length;

export const stageLabel = (value) =>
  STAGES.find((s) => s.value === value)?.label || value || '—';

/**
 * How many stages there are. Derived, never written as a literal: every
 * "stage 4 of 9" on screen reads this, so inserting a stage is one edit in the
 * array above rather than a hunt through the templates — which is exactly the
 * hunt that left stale numbers behind when Goal Approval was added.
 */
export const TOTAL_STAGES = STAGES.length;

/**
 * An objective's own lifecycle, which is not the same question as the
 * appraisal's stage. Set by the workflow from the appraisal's stage and
 * read-only on the wire — see appraisal.models.Goal.Status.
 */
export const GOAL_STATUSES = [
  { value: 'draft', label: 'Draft' },
  { value: 'approved', label: 'Approved' },
  { value: 'locked', label: 'Locked' },
];

export const goalStatusLabel = (value) =>
  GOAL_STATUSES.find((g) => g.value === value)?.label || value || 'Draft';

/**
 * Competency levels, in the model's order.
 *
 * Deliberately NOT numbered, and deliberately not given colours that run red to
 * green: a five-step colour ramp is a score with the digits filed off, and it
 * would be read as one across a table of ten competencies.
 */
export const LEVELS = [
  { value: 'needs_development', label: 'Needs Development' },
  { value: 'developing', label: 'Developing' },
  { value: 'meets', label: 'Meets Expectations' },
  { value: 'exceeds', label: 'Exceeds Expectations' },
  { value: 'outstanding', label: 'Outstanding' },
];

export const levelLabel = (value) =>
  LEVELS.find((l) => l.value === value)?.label || value || 'Not rated';

/**
 * Who recorded a rating. Values mirror CompetencyRating.RatedBy — note the
 * employee's own rating is 'employee', not 'self': the column is headed "Self"
 * because that is what a person calls their own assessment, and the wire value
 * is the model's because that is what the server will accept.
 */
export const RATER_ROLES = [
  { value: 'employee', label: 'Self' },
  { value: 'supervisor', label: 'Supervisor' },
  { value: 'committee', label: 'Committee' },
];

/**
 * Promotion readiness: three recorded answers.
 *
 * "Not Considered" is NOT in this list, and that is the point — it is the
 * absence of a value (`''`), so it can be shown as the current state of a
 * selector but can never be submitted, counted or charted beside the three real
 * answers. See appraisal.models.Appraisal.PromotionReadiness.
 */
export const READINESS = [
  { value: 'ready', label: 'Ready' },
  { value: 'ready_with_development', label: 'Ready With Development' },
  { value: 'development_required', label: 'Development Required' },
];

export const NOT_CONSIDERED = 'Not Yet Considered';

export const readinessLabel = (value) =>
  (value ? READINESS.find((r) => r.value === value)?.label || value
    : NOT_CONSIDERED);

/** The four things a training line can be. Mirrors TrainingPlan.Kind. */
export const TRAINING_KINDS = [
  { value: 'training', label: 'Training' },
  { value: 'certification', label: 'Certification' },
  { value: 'mentorship', label: 'Mentorship' },
  { value: 'on_the_job', label: 'On-the-job' },
];

export const trainingKindLabel = (value) =>
  TRAINING_KINDS.find((k) => k.value === value)?.label || value || 'Training';

export const TRAINING_STATUSES = [
  { value: 'identified', label: 'Identified' },
  { value: 'approved', label: 'Approved' },
  { value: 'scheduled', label: 'Scheduled' },
  { value: 'completed', label: 'Completed' },
  { value: 'declined', label: 'Declined' },
];

export const PRIORITIES = [
  { value: 'high', label: 'High' },
  { value: 'medium', label: 'Medium' },
  { value: 'low', label: 'Low' },
];

/** The line every evidence surface carries, in one place so it cannot drift. */
export const EVIDENCE_DISCLAIMER =
  'Recorded activity from the task module, cited as context for a human '
  + 'judgement. Nothing in this appraisal is scored, weighted, ranked or '
  + 'computed from these figures.';
