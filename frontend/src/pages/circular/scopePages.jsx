import React from 'react';

import CircularScopePage from '../../components/circular/CircularScopePage';
import { EMPTY } from '../../services/emptyStates';

/**
 * The nine queues the Phase 50 brief names, each one a scope string and a column
 * set. No filtering logic lives here - the server owns what every scope means.
 *
 * The column sets differ because the question each queue answers differs, and that
 * is the whole reason for having nine queues rather than one list with a filter:
 * a broadcast queue is read for reach, a draft queue for who is holding it up, and
 * an archive for who issued it and when.
 */
export const AllCirculars = () => (
  <CircularScopePage
    scope="all"
    title="All Circulars"
    subtitle="Every circular you can see"
    columns={['category', 'classification', 'department', 'status', 'issued', 'reach']}
    emptyMessage="No circulars are visible to you yet."
    allowCreate
  />
);

export const DraftCirculars = () => (
  <CircularScopePage
    scope="drafts"
    title="Draft Circulars"
    subtitle="Yours, not yet submitted — plus any returned to you for revision"
    columns={['category', 'classification', 'status', 'pending']}
    emptyMessage="You have no draft circulars."
    allowCreate
  />
);

export const AssignedCirculars = () => (
  <CircularScopePage
    scope="assigned"
    title="Assigned Circulars"
    subtitle="Waiting on you now, to review or to issue"
    columns={['category', 'classification', 'department', 'status', 'pending']}
    emptyMessage={EMPTY.nothingToReviewOrSign}
  />
);

export const UnderReviewCirculars = () => (
  <CircularScopePage
    scope="under_review"
    title="Under Review"
    subtitle="Submitted and with a reviewer"
    columns={['category', 'department', 'status', 'pending']}
    emptyMessage="No circulars are under review."
  />
);

export const ReadyForIssue = () => (
  <CircularScopePage
    scope="ready_for_issue"
    title="Ready For Issue"
    subtitle="Reviewed (or needing no review) and awaiting the issuer's signature"
    columns={['category', 'classification', 'department', 'status', 'pending']}
    emptyMessage="Nothing is waiting to be issued."
  />
);

export const ReadyForBroadcast = () => (
  <CircularScopePage
    scope="ready_for_broadcast"
    title="Ready For Broadcast"
    subtitle="Issued and official — awaiting an audience"
    columns={['category', 'classification', 'department', 'status', 'issued']}
    emptyMessage="Nothing is waiting to be broadcast."
  />
);

export const BroadcastedCirculars = () => (
  <CircularScopePage
    scope="broadcasted"
    title="Broadcasted Circulars"
    subtitle="In circulation — reach is live and changes as people open them"
    columns={['category', 'classification', 'department', 'issued', 'reach',
      'acknowledgement']}
    emptyMessage="No circulars are in circulation."
  />
);

export const MyAcknowledgements = () => (
  <CircularScopePage
    scope="my_acknowledgements"
    title="My Acknowledgements"
    subtitle="Circulars awaiting your confirmation. Opening one is not the same as acknowledging it."
    columns={['category', 'classification', 'issued']}
    emptyMessage="You have no outstanding acknowledgements."
    showFilters={false}
  />
);

export const UnreadCirculars = () => (
  <CircularScopePage
    scope="unread"
    title="Unread Circulars"
    subtitle="Broadcast to you and not yet opened"
    columns={['category', 'classification', 'issued']}
    emptyMessage="You have opened every circular sent to you."
    showFilters={false}
  />
);

export const ArchivedCirculars = () => (
  <CircularScopePage
    scope="archived"
    title="Archived Circulars"
    subtitle="The permanent record — read-only, exportable as PDF"
    columns={['category', 'classification', 'department', 'issued', 'reach',
      'archived']}
    emptyMessage="The archive is empty."
  />
);
