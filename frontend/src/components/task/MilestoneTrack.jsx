import React from 'react';
import { CircleCheck, CircleDashed } from 'lucide-react';

import { fmtDate } from './taskLabels';

/**
 * Created · Started · Reviewed · Completed (Phase TASK-GOVERNANCE-HARDENING).
 *
 * The shape of a task's life, beside the full audit trail rather than instead
 * of it. The trail answers "what happened and who did it", forty rows at a
 * time; this answers "how far has this got, and where did it stall", which is
 * the question somebody scanning a task actually has.
 *
 * A MILESTONE NOT REACHED IS DRAWN, NOT OMITTED. An empty Started beside a
 * filled Completed is itself information - it means the work was never picked
 * up through the system - and a track that silently dropped the gaps would look
 * like an orderly three-step process.
 *
 * Every timestamp comes from the server's own `milestones` list. Nothing here
 * infers a date from another one.
 */
const MilestoneTrack = ({ milestones = [] }) => {
  if (!milestones.length) return null;
  return (
    <ol className="ms-track" aria-label="Task milestones">
      {milestones.map((step) => {
        const reached = Boolean(step.at);
        return (
          <li key={step.key} className={`ms-step${reached ? ' is-done' : ''}`}>
            {reached
              ? <CircleCheck size={15} aria-hidden="true" />
              : <CircleDashed size={15} aria-hidden="true" />}
            <span className="ms-label">{step.label}</span>
            <span className="ms-when">
              {reached ? fmtDate(step.at) : 'Not yet'}
            </span>
          </li>
        );
      })}
    </ol>
  );
};

export default MilestoneTrack;
