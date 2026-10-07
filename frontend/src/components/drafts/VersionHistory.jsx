import React, { useState } from 'react';
import { History } from 'lucide-react';

import { draftService } from '../../services/draftService';

/**
 * Retained draft versions (Phase 111.10).
 *
 * These are milestones, not every autosave. At a five-second cadence ten
 * versions would span the last four minutes of typing and each would be
 * indistinguishable from the last; a version is written only when the user
 * saved, switched away, or crossed the interval, which is what makes the list
 * worth reading.
 *
 * Restoring is non-destructive: the server retains the copy being replaced
 * first, so restoring the wrong version is recoverable.
 */

const REASON_TEXT = {
  manual_save: 'You saved',
  tab_hidden: 'You switched away',
  interval: 'Periodic save',
  before_restore: 'Replaced by a restore',
};

const stamp = (iso) => {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString();
};

const VersionHistory = ({ kind, documentKey, versions = [], onRestored }) => {
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);

  if (!versions.length) return null;

  const restore = async (version) => {
    setBusy(version);
    setError(null);
    try {
      const draft = await draftService.restoreVersion(kind, documentKey, version);
      onRestored?.(draft?.payload);
    } catch {
      setError('That version could not be restored. Your current draft is unchanged.');
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="draft-versions" aria-labelledby="draft-versions-title">
      <h3 id="draft-versions-title" className="draft-versions-title">
        <History size={15} aria-hidden="true" />
        Draft history
        <span className="draft-versions-count">{versions.length} kept</span>
      </h3>

      {error && <p className="draft-versions-error" role="alert">{error}</p>}

      <ol className="draft-versions-list">
        {versions.map((v) => (
          <li key={v.id} className="draft-version">
            <div className="draft-version-meta">
              <span className="draft-version-no">v{v.version}</span>
              <span className="draft-version-when">{stamp(v.saved_at)}</span>
              <span className="draft-version-why">
                {REASON_TEXT[v.reason] || v.reason_label || 'Saved'}
              </span>
            </div>
            <button type="button" className="lr-btn lr-btn-ghost draft-version-restore"
              disabled={busy !== null} onClick={() => restore(v.version)}>
              {busy === v.version ? 'Restoring…' : 'Restore'}
            </button>
          </li>
        ))}
      </ol>
    </section>
  );
};

export default VersionHistory;
