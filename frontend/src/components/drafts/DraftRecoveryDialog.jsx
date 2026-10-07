import React from 'react';
import { AlertCircle, RotateCcw } from 'lucide-react';

/**
 * Recovery dialog (Phase 111.5) — Restore, Continue Editing, Discard.
 *
 * Shown when a snapshot is found on mount. The three choices are deliberately
 * not equal: Restore is the primary action because a user who lost work to a
 * crash came back to get it, and Discard is separated from the other two
 * because it is the only irreversible one.
 *
 * It states WHAT would be restored and WHEN it was saved. "You have an
 * unsaved draft" without those two facts leaves the user guessing whether
 * accepting it will overwrite something newer.
 */

const describe = (payload) => {
  if (!payload || typeof payload !== 'object') return [];
  const lines = [];
  const form = payload.form && typeof payload.form === 'object' ? payload.form : {};
  const subject = form.subject || payload.subject;
  if (subject) lines.push(`Subject: ${String(subject).slice(0, 90)}`);
  // A task draft carries a title rather than a subject.
  if (!subject && payload.title) lines.push(`Title: ${String(payload.title).slice(0, 90)}`);

  const sections = Array.isArray(payload.sections) ? payload.sections : [];
  const words = sections
    .map((s) => String(s?.body || '').replace(/<[^>]*>/g, ' '))
    .join(' ').trim().split(/\s+/).filter(Boolean).length;
  if (words) lines.push(`${words} word${words === 1 ? '' : 's'} of content`);

  const bodyWords = String(payload.agenda_body || form.agenda_body
    || payload.content || form.content || '')
    .replace(/<[^>]*>/g, ' ').trim().split(/\s+/).filter(Boolean).length;
  if (bodyWords && !words) {
    lines.push(`${bodyWords} word${bodyWords === 1 ? '' : 's'} of content`);
  }

  const people = (Array.isArray(payload.members) ? payload.members.length : 0)
    + (Array.isArray(payload.matrixRows) ? payload.matrixRows.length : 0)
    + (Array.isArray(payload.chain) ? payload.chain.length : 0);
  if (people) lines.push(`${people} person${people === 1 ? '' : 's'} in the workflow`);

  const subtasks = Array.isArray(payload.subtasks)
    ? payload.subtasks.filter((row) => String(row?.title || '').trim()).length : 0;
  if (subtasks) lines.push(`${subtasks} subtask${subtasks === 1 ? '' : 's'}`);

  const files = Array.isArray(payload.attachments) ? payload.attachments.length : 0;
  // Named as needing re-attachment because the bytes are genuinely gone — only
  // the metadata is recoverable (blocker B3).
  if (files) lines.push(`${files} attachment${files === 1 ? '' : 's'} to re-attach`);

  return lines;
};

const when = (date) => {
  if (!date) return 'a moment ago';
  const minutes = Math.round((Date.now() - date.getTime()) / 60000);
  if (minutes < 1) return 'less than a minute ago';
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
  return date.toLocaleString();
};

const DraftRecoveryDialog = ({ draft, onRestore, onContinue, onDiscard, label = 'draft' }) => {
  if (!draft) return null;
  const details = describe(draft.payload);

  return (
    <div className="draft-recovery-backdrop" role="presentation">
      <div className="draft-recovery" role="dialog" aria-modal="true"
        aria-labelledby="draft-recovery-title">
        <div className="draft-recovery-head">
          <RotateCcw size={20} aria-hidden="true" />
          <h2 id="draft-recovery-title">Unsaved {label} found</h2>
        </div>

        <p className="draft-recovery-lede">
          You have an unsaved {label} from <strong>{when(draft.savedAt)}</strong>
          {draft.source === 'local'
            ? ' saved on this device.'
            : `${draft.device ? ` saved from ${draft.device}.` : ' saved on the server.'}`}
        </p>

        {details.length > 0 && (
          <ul className="draft-recovery-detail">
            {details.map((line) => <li key={line}>{line}</li>)}
          </ul>
        )}

        {draft.source === 'local' && (
          <p className="draft-recovery-note">
            <AlertCircle size={13} aria-hidden="true" />
            This copy is newer than the one on the server.
          </p>
        )}

        <div className="draft-recovery-actions">
          <button type="button" className="lr-btn lr-btn-primary" onClick={onRestore}>
            Restore draft
          </button>
          <button type="button" className="lr-btn lr-btn-ghost" onClick={onContinue}>
            Continue editing
          </button>
          <button type="button" className="lr-btn lr-btn-ghost draft-recovery-discard"
            onClick={onDiscard}>
            Discard draft
          </button>
        </div>
      </div>
    </div>
  );
};

export default DraftRecoveryDialog;
