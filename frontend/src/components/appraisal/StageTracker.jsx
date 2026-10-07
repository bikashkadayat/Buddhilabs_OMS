import React from 'react';
import { Check } from 'lucide-react';

import {
  EMPLOYEE_STEPS, STAGES, isComplete, stepForIndex, stepForStatus, stepNumber,
} from './appraisalLabels';

/**
 * The nine-stage tracker (Phase APM-03b).
 *
 * WHY THE LADDER IS RENDERED AND NOT JUST THE CURRENT STAGE
 * ---------------------------------------------------------
 * "Supervisor Review" tells somebody where their appraisal is. It does not tell
 * them how much is left, who has it next, or that a self-assessment they have
 * not written is what everything after it is waiting on. The tracker is the
 * only place in this module that answers "what happens after this".
 *
 * ACCESSIBILITY
 * -------------
 * An ordered list, not a row of divs, so a screen reader announces "step 4 of
 * 9" from the markup rather than from a colour. The current step carries
 * aria-current, and completed steps say "completed" in text — the tick is
 * decorative and the colour carries no information on its own.
 */
/**
 * The employee's five-step view (Phase APM-UX).
 *
 * Ten stages is what the ORGANISATION runs. Five is what the person being
 * appraised has to hold in their head, and the difference between the two is
 * the difference between a process somebody can use in five minutes and one
 * they need training for.
 *
 * Nothing is hidden that they would need: the committee's involvement still
 * appears in their Feedback, attributed. What goes is the demand that they
 * learn a ten-step vocabulary before they can find out whose turn it is.
 */
const SimpleTracker = ({ step, complete }) => {
  const current = stepNumber(step);
  return (
    <nav className="apr-stages is-simple" aria-label="Where your appraisal is">
      <p className="sr-only">
        {complete
          ? 'Your appraisal is complete.'
          : `Step ${current} of ${EMPLOYEE_STEPS.length}: ${step?.label}`}
      </p>
      <ol className="apr-stage-list">
        {EMPLOYEE_STEPS.map((entry, i) => {
          const position = i + 1;
          const done = complete || position < current;
          const here = !complete && position === current;
          return (
            <li key={entry.key}
              className={`apr-stage${done ? ' is-done' : ''}${here ? ' is-current' : ''}`}
              aria-current={here ? 'step' : undefined}>
              <span className="apr-stage-dot" aria-hidden="true">
                {done ? <Check size={11} /> : position}
              </span>
              <span className="apr-stage-label">
                {entry.label}
                {here && <span className="apr-stage-hint">{entry.hint}</span>}
              </span>
              {done && <span className="sr-only"> — done</span>}
            </li>
          );
        })}
      </ol>
    </nav>
  );
};

const StageTracker = ({ status, stageIndex, totalStages = STAGES.length,
                        simple = false }) => {
  // `stage_index` is ONE-BASED and comes from the server (see
  // Appraisal.stage_index). Falling back to a local lookup keeps the tracker
  // drawable from a list row, which carries the status but not always the
  // index — and that fallback is also one-based, so the two agree.
  const index = stageIndex
    ?? (STAGES.findIndex((s) => s.value === status) + 1);

  // The employee's view. Derived from the raw `status` where it was sent and
  // from the ladder POSITION otherwise — never from the display label, which a
  // rewording would silently change underneath this.
  if (simple) {
    const step = stepForStatus(status) || stepForIndex(index);
    return <SimpleTracker step={step} complete={isComplete(status, index)} />;
  }

  return (
    <nav className="apr-stages" aria-label="Appraisal stages">
      {/* One string, not an interpolated fragment: split across JSX
          expressions this becomes several text nodes, and a screen reader can
          pause between them mid-sentence. */}
      <p className="sr-only">
        {`Stage ${index} of ${totalStages}: ${STAGES[index - 1]?.label || status}`}
      </p>
      <ol className="apr-stage-list">
        {STAGES.map((stage, i) => {
          const position = i + 1;
          const done = position < index;
          const current = position === index;
          return (
            <li key={stage.value}
              className={`apr-stage${done ? ' is-done' : ''}${current ? ' is-current' : ''}`}
              aria-current={current ? 'step' : undefined}>
              <span className="apr-stage-dot" aria-hidden="true">
                {done ? <Check size={11} /> : position}
              </span>
              <span className="apr-stage-label">{stage.label}</span>
              {done && <span className="sr-only"> — completed</span>}
            </li>
          );
        })}
      </ol>
    </nav>
  );
};

export default StageTracker;
