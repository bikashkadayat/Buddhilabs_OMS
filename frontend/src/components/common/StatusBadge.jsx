import React from 'react';

/**
 * Status and record-type badges (Phase D / blueprint §21).
 *
 * TWO SEPARATE FAMILIES, deliberately. `status` says where a record is in its
 * workflow; `type` says what kind of record it is. They use different palettes
 * so a violet MEMO tag can never be misread as a status.
 *
 * Colour is never the only signal: every badge carries a WORD, so the meaning
 * survives greyscale printing - and these records get printed - as well as
 * colour-vision deficiency.
 */
const STATUS = {
  draft: ['is-mute', 'Draft'],
  pending: ['is-warn', 'Pending'],
  pending_hr: ['is-warn', 'Pending HR'],
  under_review: ['is-info', 'In review'],
  in_review: ['is-info', 'In review'],
  submitted: ['is-info', 'Submitted'],
  recommended: ['is-info', 'Recommended'],
  supported: ['is-info', 'Supported'],
  approved: ['is-good', 'Approved'],
  acknowledged: ['is-good', 'Acknowledged'],
  issued: ['is-good', 'Issued'],
  broadcasted: ['is-good', 'Broadcast'],
  rejected: ['is-bad', 'Rejected'],
  // Inventory lifecycle (Phase D1). Colours follow the brief: available green,
  // assigned blue, maintenance orange, retired grey, disposed red. The two
  // pre-stock statuses are warm because both mean "not yet issuable".
  available: ['is-good', 'Available'],
  assigned: ['is-info', 'Assigned'],
  out: ['is-warn', 'Taken out'],
  maintenance: ['is-warn', 'Maintenance'],
  procurement: ['is-warn', 'On order'],
  received: ['is-info', 'Awaiting check-in'],
  retired: ['is-mute', 'Retired'],
  disposed: ['is-bad', 'Disposed'],
  cancelled: ['is-mute', 'Cancelled'],
  overdue: ['is-bad', 'Overdue'],
  archived: ['is-mute', 'Archived'],
  // The organization lifecycle (Phase S6). Added here rather than given its
  // own badge in the platform console, because the console is part of the
  // product and a second badge component is how two palettes start.
  //
  // `grace` is warn, not good: a tenant in its grace period IS still working
  // normally, and the whole reason to show the state separately is that
  // somebody needs to phone them before it ends.
  provisioning: ['is-warn', 'Provisioning'],
  trial: ['is-info', 'Trial'],
  active: ['is-good', 'Active'],
  grace: ['is-warn', 'Grace period'],
  suspended: ['is-bad', 'Suspended'],
  expired: ['is-bad', 'Expired'],
  verified: ['is-good', 'Verified'],
  awaiting_proof: ['is-warn', 'Awaiting proof'],
};

const TYPES = {
  memo: 'MEMO', minute: 'MINUTE', circular: 'CIRCULAR',
  leave: 'LEAVE', asset: 'ASSET', attendance: 'ATTENDANCE',
};

const normalise = (v) => String(v || '').toLowerCase().replace(/[\s-]+/g, '_');

const StatusBadge = ({ status, type, label }) => {
  if (type) {
    const key = normalise(type);
    return <span className={`ui-type ui-type-${key}`}>{label || TYPES[key] || type}</span>;
  }
  const key = normalise(status);
  const [tone, text] = STATUS[key] || ['is-mute', label || status || '—'];
  return <span className={`ui-badge ${tone}`}>{label || text}</span>;
};

export default StatusBadge;
