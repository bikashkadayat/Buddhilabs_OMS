import React from 'react';
import { CheckCircle2, Clock } from 'lucide-react';

import { fmtDate } from './taskLabels';

/**
 * Who a task is assigned to, and whether they have accepted it
 * (Phase TASK-DETAIL-UI-POLISH).
 *
 * ROWS, NOT PILLS
 * ---------------
 * This was a wrapping row of chips, each carrying a name plus two `<em>`
 * clauses. Any name long enough — or any viewport narrow enough — broke a chip
 * across two lines, and a pill that wraps reads as two people rather than one.
 * Widening the chip only moved the width at which it happened.
 *
 * A row per person cannot overlap at any width, because nothing is competing
 * for the same line: initials, name, then the acceptance badge, which drops
 * beneath the name on a phone instead of being squeezed into three characters.
 *
 * ACCEPTANCE IS SAID, NOT COLOURED
 * --------------------------------
 * The badge carries an icon AND the word ("Accepted" / "Not accepted"), with
 * colour as a third signal rather than the only one. Colour alone would leave
 * the one fact this card exists to convey unreadable to anybody who cannot
 * separate the two hues.
 */
const initials = (name) => (name || '?')
  .split(/\s+/).filter(Boolean).slice(0, 2)
  .map((part) => part[0]).join('').toUpperCase();

const AssigneeList = ({ rows = [] }) => {
  if (!rows.length) return <p className="task-sub">Nobody is assigned yet.</p>;

  return (
    <ul className="task-people">
      {rows.map((row) => (
        <li key={row.id} className="task-person">
          <span className="task-person-avatar" aria-hidden="true">
            {initials(row.user_name)}
          </span>
          <span className="task-person-id">
            <span className="task-person-name">{row.user_name || 'Unknown'}</span>
            {row.is_primary && (
              // The person answerable when several are assigned. A quiet tag:
              // it qualifies the name, it is not a second status.
              <span className="task-person-tag">primary</span>
            )}
          </span>
          <span className={`task-person-state ${row.has_accepted ? 'is-yes' : 'is-no'}`}>
            {row.has_accepted ? (
              <>
                <CheckCircle2 size={13} aria-hidden="true" />
                Accepted <span className="task-person-when">{fmtDate(row.accepted_at)}</span>
              </>
            ) : (
              <>
                <Clock size={13} aria-hidden="true" />
                Not accepted
              </>
            )}
          </span>
        </li>
      ))}
    </ul>
  );
};

export default AssigneeList;
