import React from 'react';
import { Check, Clock, Minus, X } from 'lucide-react';

const fmt = (iso) => (iso ? new Date(iso).toLocaleDateString(undefined, {
  month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
}) : null);

// The mark drawn inside each node. Shape distinguishes the five states without
// relying on colour: a tick for done, a clock for the stage in progress, a cross
// for a rejection, a dash for a stage this chain never included, and an empty
// ring for one still ahead.
const MARK = {
  done: { Icon: Check, label: 'Completed' },
  active: { Icon: Clock, label: 'In progress' },
  rejected: { Icon: X, label: 'Rejected' },
  skipped: { Icon: Minus, label: 'Not required' },
  pending: { Icon: null, label: 'Pending' },
};

/**
 * The Phase 18 visual workflow tracker:
 *
 *   Created → Recommended → Supported → Approved → Archived
 *
 * Always five nodes, whatever the memo's actual chain contains. A stage the chain
 * omits reads "Not required" rather than being hidden, because a tracker that
 * silently drops stages makes two different memos look like the same process and
 * gives the reader no way to tell that a step was skipped on purpose.
 *
 * Horizontal on wide screens, vertical on narrow ones — a five-node horizontal
 * rail on a phone either overflows or shrinks its labels to nothing.
 *
 * @param {{ stages:Array<Object> }} props from `memo.tracker`
 */
const WorkflowTracker = ({ stages = [] }) => {
  if (!stages.length) return null;

  return (
    <div className="memo-panel">
      <h3 className="memo-panel-title">Workflow Progress</h3>
      <ol className="memo-tracker" aria-label="Workflow progress">
        {stages.map((stage, index) => {
          const meta = MARK[stage.state] || MARK.pending;
          const { Icon } = meta;
          const at = fmt(stage.at);
          return (
            <li key={stage.key} className={`memo-track-step is-${stage.state}`}>
              {index > 0 && <span className="memo-track-line" aria-hidden="true" />}
              <span className="memo-track-node">
                {Icon ? <Icon size={13} aria-hidden="true" /> : <span className="memo-track-dot" aria-hidden="true" />}
              </span>
              <span className="memo-track-body">
                <span className="memo-track-label">{stage.label}</span>
                {/* The state is spelled out, so it is never colour-only. */}
                <span className="memo-track-state">{meta.label}</span>
                {stage.actor && <span className="memo-track-actor">{stage.actor}</span>}
                {at && <time className="memo-track-at" dateTime={stage.at}>{at}</time>}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
};

export default WorkflowTracker;
