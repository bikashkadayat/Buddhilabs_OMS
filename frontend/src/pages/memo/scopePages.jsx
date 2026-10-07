import React from 'react';
import MemoScopePage from '../../components/memo/MemoScopePage';
import { EMPTY } from '../../services/emptyStates';

/**
 * The memo sidebar menus.
 *
 * Every one of these is the same page bound to a different server-side scope, so
 * a menu's definition lives in exactly one place (memos.views._apply_scope) and
 * adding a menu is a route plus a scope name, not a new list implementation with
 * its own filtering bugs. The column sets differ because the question each menu
 * answers differs — an inbox needs "how long has this been waiting on me", an
 * archive needs "when was it approved".
 */

export const DraftMemos = () => (
  <MemoScopePage
    scope="drafts"
    title="Draft Memo"
    subtitle="Memos you have started but not yet sent for review"
    columns={['type', 'author', 'created']}
    emptyMessage="You have no draft memos."
    hideStatusFilter
    allowCreate
  />
);

export const DraftForReview = () => (
  <MemoScopePage
    scope="draft_for_review"
    title="Draft For Review"
    subtitle="Sent for review and awaiting the first action"
    columns={['type', 'pending', 'pendingSince', 'created']}
    emptyMessage="Nothing is awaiting a first review."
    hideStatusFilter
  />
);

export const DepartmentMemos = () => (
  <MemoScopePage
    scope="department"
    title="Department Memo"
    subtitle="Memos raised by your department"
    // Phase 12 item 6: Department | Created By | Status | Current Assignee | Created.
    columns={['department', 'author', 'status', 'pending', 'created']}
    emptyMessage="No memos have been raised in your department."
    showDepartmentFilter
    showDateFilter
    allowExport
  />
);

export const MyPendingActions = () => (
  <MemoScopePage
    scope="pending"
    title="My Pending Actions"
    subtitle="Memos waiting on your decision right now"
    columns={['type', 'author', 'pendingSince', 'dueDays', 'action']}
    emptyMessage={EMPTY.nothingWaiting}
    hideStatusFilter
    // Someone working a queue needs it to stay current without a manual reload.
    refetchInterval={30_000}
  />
);

export const MemoInbox = () => (
  <MemoScopePage
    scope="inbox"
    title="Memo Inbox"
    subtitle="Everything routed to you — including steps ahead of your turn"
    // Phase 12 item 7: Memo No | Subject | Department | Requested By |
    // Pending Since | Due Days | Priority | Action.
    columns={['department', 'author', 'status', 'pendingSince', 'dueDays', 'action']}
    emptyMessage="Your inbox is empty."
    refetchInterval={30_000}
  />
);

/**
 * Every memo the current user created, at any status - Draft through Archived and
 * Rejected - with the status filter available to narrow it.
 *
 * Deliberately the full set rather than only the in-flight ones: this is the
 * author's own record of what they have raised, and a memo vanishing from it the
 * moment it was approved would make the menu useless for looking anything up.
 * `/memos/my` renders the same view, which is where the dashboard links.
 */
export const MemoOutbox = () => (
  <MemoScopePage
    scope="mine"
    title="Memo Outbox"
    subtitle="Every memo you have created, at any stage"
    columns={['type', 'status', 'pending', 'pendingSince', 'created']}
    emptyMessage="You have not created any memos yet."
    showDateFilter
    allowExport
    allowCreate
  />
);

export const ApprovedMemos = () => (
  <MemoScopePage
    scope="approved"
    title="Approved Memo"
    subtitle="Fully approved memos"
    columns={['type', 'status', 'author', 'department', 'approvedBy', 'approved']}
    emptyMessage="No memos have been approved yet."
    hideStatusFilter
    showDateFilter
    allowExport
  />
);

export const ArchivedMemos = () => (
  <MemoScopePage
    scope="archived"
    title="Archived Memo"
    subtitle="The permanent memo record — read-only"
    columns={['type', 'author', 'department', 'approvedBy', 'approved', 'archived']}
    emptyMessage="The archive is empty."
    hideStatusFilter
    showDepartmentFilter
    showDateFilter
    allowExport
  />
);

export const RejectedMemos = () => (
  <MemoScopePage
    scope="rejected"
    title="Rejected Memo"
    subtitle="Memos returned with comments for revision"
    columns={['type', 'author', 'created']}
    emptyMessage="No memos have been rejected."
    hideStatusFilter
  />
);

export const AllMemos = () => (
  <MemoScopePage
    scope="all"
    title="All Memos"
    subtitle="Every memo visible to you"
    columns={['type', 'status', 'pending', 'author', 'created']}
    emptyMessage="No memos are visible to you yet."
    showDepartmentFilter
    showDateFilter
    allowExport
    allowCreate
  />
);
