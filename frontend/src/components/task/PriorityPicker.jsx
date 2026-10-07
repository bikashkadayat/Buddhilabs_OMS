import React, { useId } from 'react';

import { TASK_PRIORITIES, priorityTone } from './taskLabels';

/**
 * Priority, as the coloured badges it is read as everywhere else
 * (Phase TASK-CREATE-UI-POLISH).
 *
 * It was a `<select>`, which meant the one field on the form with a settled
 * visual language — the same `.min-status` badge the card, the list and the
 * detail header all use — was the one field that showed none of it. You chose
 * "Urgent" from a grey dropdown and only discovered it was red after saving.
 *
 * Radio inputs under the badges, for the reason the progress control uses them:
 * this is one choice among four, arrow keys should move through it, and the
 * selected one should be announced as selected rather than merely coloured.
 *
 * THE FOUR LEVELS ARE THE SERVER'S. `TASK_PRIORITIES` is the stored vocabulary
 * (low / medium / high / urgent); this file picks none of its own.
 */
const PriorityPicker = ({ value, onChange }) => {
  const group = useId();

  return (
    <div className="task-priority-pick" role="radiogroup" aria-label="Priority">
      {TASK_PRIORITIES.map((option) => (
        <label key={option.value}
          className={`task-priority-opt${value === option.value ? ' is-on' : ''}`}>
          <input type="radio" name={`priority-${group}`} value={option.value}
            checked={value === option.value}
            onChange={() => onChange(option.value)} />
          <span className={`min-status is-${priorityTone(option.value)}`}>
            {option.label}
          </span>
        </label>
      ))}
    </div>
  );
};

export default PriorityPicker;
