import React from 'react';
import { Check, CloudOff, HardDrive, RefreshCw } from 'lucide-react';
import { STATUS } from '../../hooks/useDocumentDraft';

/**
 * Autosave status (Phase 111.8) — the quiet line that says work is safe.
 *
 * Reports STORAGE, never approval stage. The two must not blur: an autosave
 * concept leaking into a document's workflow status would show up in every
 * dashboard tile, filter, permission check and PDF in the system, which is the
 * condition the feature was approved under (drafts/tests/test_workflows_untouched.py).
 *
 * Renders nothing until something has actually been saved — "Saved" on an
 * untouched form is a lie, and a reassurance the user did not earn is worse
 * than silence.
 */

const META = {
  [STATUS.SAVING]: { cls: 'is-saving', label: 'Saving…', Icon: RefreshCw },
  [STATUS.SAVED]: { cls: 'is-saved', label: 'Saved', Icon: Check },
  [STATUS.OFFLINE]: { cls: 'is-offline', label: 'Offline draft', Icon: CloudOff },
  [STATUS.LOCAL_ONLY]: { cls: 'is-local', label: 'Local only', Icon: HardDrive },
};

/** "just now" / "2 minutes ago" / "3 hours ago". Coarse on purpose: a
 *  to-the-second stamp invites people to watch it instead of writing. */
const agoLabel = (value) => {
  if (!value) return '';
  const then = value instanceof Date ? value.getTime() : new Date(value).getTime();
  if (Number.isNaN(then)) return '';
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (secs < 45) return 'just now';
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins} minute${mins === 1 ? '' : 's'} ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? '' : 's'} ago`;
};

/**
 * `savedLabel` lets a caller say what was saved — the task form reads
 * "Draft saved", because on that page a plain "Saved" sits next to a Save
 * Changes button and reads as though the task itself had been written.
 */
const SaveIndicator = ({ status = STATUS.IDLE, lastSavedAt = null, savedLabel = 'Saved' }) => {
  const meta = META[status];
  if (!meta) return null; // IDLE, or a status this component does not speak for

  const { cls, Icon } = meta;
  const label = status === STATUS.SAVED ? savedLabel : meta.label;
  // No timestamp while a save is in flight: the stamp would describe the
  // PREVIOUS save and read as though the current one had already finished.
  const stamp = status === STATUS.SAVING ? '' : agoLabel(lastSavedAt);

  return (
    <span className={`draft-indicator ${cls}`} role="status" aria-live="polite">
      <Icon className="draft-indicator-icon" size={13} aria-hidden="true" />
      {label}
      {stamp && <span className="draft-indicator-stamp">{stamp}</span>}
    </span>
  );
};

export default SaveIndicator;
