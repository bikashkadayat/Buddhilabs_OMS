import React, { useEffect, useId, useRef, useState } from 'react';
import { Sparkles } from 'lucide-react';

/**
 * Progress (Phase T2.2, control redesigned in TASK-DETAIL-UI-POLISH).
 *
 * WHY FIVE STEPS AND NOT A FREE SLIDER
 * ------------------------------------
 * The specification asks for 0 / 25 / 50 / 75 / 100, and it is right to: a
 * free-running slider invites 63%, which is a number nobody can act on and
 * nobody can defend in a review. Five steps mean "not started", "begun", "half",
 * "nearly", "done" — which is what the figure is actually being asked for.
 *
 * The API still accepts any 0–100, so a figure recorded by an integration or by
 * the checklist is displayed faithfully even when it is not one of the five;
 * the control simply does not offer to CREATE one.
 *
 * A SEGMENTED RADIO GROUP, NOT FIVE BUTTONS
 * -----------------------------------------
 * Five separate buttons were five separate tab stops that announced themselves
 * as unrelated actions, and the only thing marking the current value was a
 * colour on one of them. These are what they always were — one choice with five
 * options — so they are real radio inputs in one group: arrow keys move between
 * them, the selected one is announced as selected, and the segmented styling is
 * the same affordance a person already reads as "pick one of these".
 *
 * WHEN IT IS AUTOMATIC, IT IS NOT EDITABLE
 * ----------------------------------------
 * While the task has a checklist and nobody has overridden the figure, the boxes
 * ARE the progress and the control would only offer a way to make the two
 * disagree. The banner says so rather than leaving a disabled control with no
 * explanation.
 */
const STEPS = [
  { value: 0, label: 'Not started' },
  { value: 25, label: 'Begun' },
  { value: 50, label: 'Half done' },
  { value: 75, label: 'Nearly done' },
  { value: 100, label: 'Complete' },
];

/**
 * `percent` is the TASK's figure — the headline bar and the number beside it.
 * `selected` is what THIS PERSON last reported, which is what the segments
 * choose between (Phase TASK-DETAIL-UI-POLISH).
 *
 * They are the same number on a solo task and different on a shared one, where
 * the task's figure is the mean of everybody's. Checking a segment against the
 * mean was wrong twice over: a radio group would show nothing selected the
 * moment three reports averaged to 65, and what it did show was somebody
 * else's work presented as this person's own answer.
 */
const ProgressControl = ({
  percent = 0, selected = null, checklistPercent = null, isAuto = true,
  hasChecklist = false, canUpdate = false, busy = false, onChange,
}) => {
  const derived = hasChecklist && isAuto;
  // Falls back to the task's figure, which is what a solo assignee reported.
  const reported = selected === null || selected === undefined ? percent : selected;

  /**
   * ONE WRITE PER DECISION, NOT PER KEYSTROKE.
   *
   * A radio group is navigated with the arrow keys, and each arrow press
   * selects — so a keyboard user moving 0 → 100 fires four reports, four
   * timeline rows and (at 100) a notification to the whole team. The five
   * buttons this replaced took one Tab-and-Enter to do the same thing, and
   * trading that for audit noise would undo work the previous phase just did.
   *
   * So the selection moves immediately and the REPORT follows a short pause:
   * a run of arrow presses sends the value the person stopped on. Nothing
   * about what is sent changes — only how many times.
   */
  // The local choice remembers what the server said WHEN it was made, so it
  // retires by itself the moment the server says something new — derived
  // rather than synchronised, so there is no effect to leave stale.
  const [chosen, setChosen] = useState(null);
  const timer = useRef(null);
  const shown = chosen && chosen.base === reported ? chosen.value : reported;

  useEffect(() => () => clearTimeout(timer.current), []);

  const choose = (value) => {
    setChosen({ value, base: reported });
    clearTimeout(timer.current);
    timer.current = setTimeout(() => onChange(value), 350);
  };
  // One group name per mounted control, so two of them on a page (a task and a
  // subtask, say) cannot share a selection.
  const group = useId();

  return (
    <>
      <div className="task-bar task-bar-lg" role="img"
        aria-label={`${percent}% complete`}>
        <span className="task-bar-fill" style={{ width: `${percent}%` }} />
      </div>

      <p className="task-progress-line">
        <b>{percent}%</b>
        {checklistPercent !== null && checklistPercent !== undefined && (
          // Both numbers when they can differ, never one silently preferred:
          // they answer different questions once somebody has overridden.
          <span className="task-sub"> · checklist {checklistPercent}%</span>
        )}
        {derived && (
          <span className="task-auto">
            <Sparkles size={11} aria-hidden="true" /> from the checklist
          </span>
        )}
      </p>

      {canUpdate && derived && (
        <p className="task-sub">
          Tick the checklist to move this. Setting a figure by hand below will
          take over from it.
        </p>
      )}

      {canUpdate && (
        <div className="task-segmented" role="radiogroup"
          aria-label="Set progress">
          {STEPS.map((step) => (
            <label key={step.value}
              className={`task-segment${shown === step.value ? ' is-on' : ''}`}>
              <input
                type="radio"
                name={`progress-${group}`}
                value={step.value}
                checked={shown === step.value}
                disabled={busy}
                onChange={() => choose(step.value)}
              />
              <span className="task-segment-pct">{step.value}%</span>
              {/* What the number MEANS, so the choice is read rather than
                  guessed. Hidden on the narrowest screens, where the
                  percentages alone have to carry it. */}
              <span className="task-segment-word">{step.label}</span>
            </label>
          ))}
        </div>
      )}
    </>
  );
};

export default ProgressControl;
