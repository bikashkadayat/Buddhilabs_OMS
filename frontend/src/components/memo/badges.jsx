import React from 'react';
import { Lock } from 'lucide-react';
import {
  ROLE_COLORS, ROLE_LABELS, STATUS_COLORS, STATUS_LABELS, STEP_COLORS,
  STEP_LABELS, TYPE_COLORS, TYPE_LABELS,
} from './memoLabels';

/**
 * Memo badges. Colour + text label, never colour alone.
 *
 * The label maps live in ./memoLabels so this module exports only components
 * (React Fast Refresh requires that split, and the non-badge callers — filter
 * dropdowns, the role <select> — need the labels without the components).
 */

/** Small coloured pill. */
const Pill = ({ label, bg, fg, border }) => (
  <span className="lr-memo-pill" style={{ background: bg, color: fg, borderColor: border || bg }}>
    {label}
  </span>
);

/** @param {{status:string}} props */
export const MemoStatusBadge = ({ status }) => {
  const [bg, fg] = STATUS_COLORS[status] || STATUS_COLORS.draft;
  return <Pill label={STATUS_LABELS[status] || status} bg={bg} fg={fg} />;
};

/**
 * The manual's Memo Type (p.4): GENERAL / CONFIDENTIAL / DRAFT.
 *
 * A padlock is shown on CONFIDENTIAL — the one value that actually restricts who
 * may read the memo — so the badge says something about access rather than only
 * setting a tone. This absorbed the separate classification badge that used to
 * sit beside it, because the manual has one dropdown, not two.
 */
export const MemoTypeBadge = ({ memo_type }) => {
  const value = memo_type || 'general';
  const [bg, fg] = TYPE_COLORS[value] || TYPE_COLORS.general;
  return (
    <span className="lr-memo-pill memo-classification"
      style={{ background: bg, color: fg, borderColor: bg }}>
      {value === 'confidential' && <Lock size={10} aria-hidden="true" />}
      {TYPE_LABELS[value] || value}
    </span>
  );
};

/** Approval-matrix role type (Reviewer / Recommender / Supporter / Approver). */
export const MemoRoleBadge = ({ role_type }) => {
  const [bg, fg] = ROLE_COLORS[role_type] || ROLE_COLORS.reviewer;
  return <Pill label={ROLE_LABELS[role_type] || role_type} bg={bg} fg={fg} />;
};

/** Per-step status inside the approval matrix. */
export const MemoStepBadge = ({ status }) => {
  const [bg, fg] = STEP_COLORS[status] || STEP_COLORS.pending;
  return <Pill label={STEP_LABELS[status] || status} bg={bg} fg={fg} />;
};

