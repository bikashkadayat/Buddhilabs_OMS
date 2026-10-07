import React from 'react';
import MinuteScopePage from '../../components/minute/MinuteScopePage';
import { EMPTY } from '../../services/emptyStates';

/**
 * The minute lists.
 *
 * The manual's own menus (E-minute-manual p.3, p.7) are Draft Minute, Draft for Review
 * Minute, Assigned Minute (Initiated), Assigned Minute (Involvement), Underprocess
 * Minute and Archived Minutes. All six are here and routed. The sidebar shows the two
 * consolidated queues this HRMS already used - Needs My Action and My Minutes -
 * because four of the six each answer a slice of "is anything wanted from me?"; the
 * narrower ones stay reachable for anyone who has bookmarked them.
 *
 * Each page is the same component bound to a different server-side scope, so a list's
 * definition lives in exactly one place (minutes.views._apply_scope).
 */

/** The one queue that answers "is anything wanted from me?". */
export const NeedsMyAction = () => (
  <MinuteScopePage
    scope="needs_me"
    title="Needs My Action"
    subtitle="Minutes waiting on you — to review as FRO, or to acknowledge"
    columns={['type', 'date', 'author', 'status', 'action']}
    emptyMessage={EMPTY.nothingWaiting}
    refetchInterval={30_000}
  />
);

/** Everything I raised, at any status — drafts included. */
export const MyMinutes = () => (
  <MinuteScopePage
    scope="mine"
    title="My Minutes"
    subtitle="Every minute you have written, including drafts"
    columns={['type', 'date', 'status', 'acknowledgement']}
    emptyMessage="You have not written any minutes yet."
    allowCreate
  />
);

export const AllMinutes = () => (
  <MinuteScopePage
    scope="all"
    title="All Minutes"
    subtitle="Every minute you are allowed to see"
    columns={['type', 'date', 'status', 'author']}
    emptyMessage="No minutes are visible to you yet."
    showStatusFilter
    allowCreate
  />
);

/** The manual's archive list, column for column (p.10). */
export const ArchivedMinutes = () => (
  <MinuteScopePage
    scope="archived"
    title="Archived Minutes"
    subtitle="Acknowledged by every member present — read-only, downloadable"
    columns={['reference', 'date', 'type', 'author', 'acknowledgement', 'archived']}
    emptyMessage="The archive is empty."
  />
);

/* --- the manual's narrower menus: routed, not in the sidebar ---------------- */

export const DraftMinutes = () => (
  <MinuteScopePage
    scope="drafts"
    title="Draft Minute"
    subtitle="Minutes you have started but not yet submitted"
    columns={['type', 'date']}
    emptyMessage="You have no draft minutes."
    allowCreate
  />
);

export const DraftForReview = () => (
  <MinuteScopePage
    scope="draft_for_review"
    title="Draft for Review Minute"
    subtitle="Sent to the FRO for a draft review"
    columns={['type', 'date', 'pending']}
    emptyMessage="Nothing is with a reviewer."
    refetchInterval={30_000}
  />
);

export const AssignedInitiated = () => (
  <MinuteScopePage
    scope="initiated"
    title="Assigned Minute (Initiated)"
    subtitle="Minutes you raised that are still in progress"
    columns={['type', 'date', 'status', 'pending']}
    emptyMessage="You have no minutes in progress."
    allowCreate
  />
);

export const AssignedInvolvement = () => (
  <MinuteScopePage
    scope="involvement"
    title="Assigned Minute (Involvement)"
    subtitle="Minutes routed to you — as FRO, a member, or for information"
    columns={['type', 'date', 'author', 'status', 'action']}
    emptyMessage="Nothing has been routed to you."
    refetchInterval={30_000}
  />
);

export const UnderProcessMinutes = () => (
  <MinuteScopePage
    scope="under_process"
    title="Underprocess Minute"
    subtitle="Under draft review, or waiting on acknowledgements"
    columns={['type', 'date', 'author', 'status', 'pending', 'acknowledgement']}
    emptyMessage="No minutes are in process."
    refetchInterval={30_000}
  />
);

export const MyPendingAcknowledgements = () => (
  <MinuteScopePage
    scope="my_acknowledgements"
    title="To Acknowledge"
    subtitle="Minutes of meetings you attended, waiting for your acknowledgement"
    columns={['type', 'date', 'author', 'action']}
    emptyMessage="You have nothing left to acknowledge."
  />
);

export const MinutesAcknowledged = () => (
  <MinuteScopePage
    scope="acknowledged"
    title="Minutes Acknowledged"
    subtitle="Minutes you have already acknowledged"
    columns={['type', 'date', 'author', 'acknowledgement']}
    emptyMessage="You have not acknowledged any minutes yet."
  />
);
