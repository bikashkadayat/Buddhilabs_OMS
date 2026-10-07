import React from 'react';

/**
 * Team Progress — who has done how much, on a shared task
 * (Phase TASK-DETAIL-UI-POLISH).
 *
 * ITS OWN CARD, NOT A FOOTNOTE UNDER THE SLIDER
 * ---------------------------------------------
 * The breakdown used to hang below the Progress control, where it read as an
 * appendix to the overall number. On a task three people are doing it is the
 * more informative of the two: the overall figure says 57%, and only the
 * breakdown says that one person is finished and another has barely started.
 *
 * FOUR COLUMNS, ONE GRID
 * ----------------------
 * Name, bar, percentage, acceptance — laid out as a grid so every bar starts
 * at the same x and the figures read down a column. Names of different lengths
 * stepping the bars in and out is a table pretending not to be one.
 *
 * "NOT REPORTED" IS NOT 0%
 * ------------------------
 * A person who has said nothing gets an empty track and the words "not
 * reported", never a 0% bar: silence is not a claim to have done nothing, and
 * drawing it as one misrepresents them to everybody reading the card. The
 * server sends `null` for exactly this reason.
 */
/**
 * WITH SUBTASKS, THE COUNT IS THE FIGURE (Phase TASK-SUBTASKS)
 * ------------------------------------------------------------
 * While the task has subtasks nobody reports a percentage — the server derives
 * it — so each person's row shows how many of THEIR subtasks are done rather
 * than a number they never entered. Somebody with no subtasks assigned says
 * so, in words, for the same reason "not reported" is not 0%.
 */
const TeamProgress = ({ rows = [], overall = 0, subtasks = [] }) => {
  const bySubtasks = subtasks.length > 0;
  const tally = (row) => {
    const mine = subtasks.filter((s) => s.assignee && s.assignee === row.user?.id);
    const done = mine.filter((s) => s.is_done).length;
    return { assigned: mine.length, done };
  };
  const totalDone = subtasks.filter((s) => s.is_done).length;
  const overallPct = bySubtasks
    ? Math.round((totalDone / subtasks.length) * 100) : overall;

  return (
    <section className="task-section" aria-labelledby="task-team-progress">
      <h2 id="task-team-progress">Team Progress</h2>

      <ul className="task-team">
        {rows.map((row) => {
          if (bySubtasks) {
            const { assigned, done } = tally(row);
            const pct = assigned ? Math.round((done / assigned) * 100) : 0;
            return (
              <li key={row.id} className="task-team-row">
                <span className="task-team-name" title={row.user_name}>
                  {row.user_name}
                </span>
                <span className={`task-bar${assigned ? '' : ' is-empty'}`} role="img"
                  aria-label={assigned
                    ? `${row.user_name}: ${done} of ${assigned} subtasks done`
                    : `${row.user_name}: no subtasks assigned`}>
                  <span className="task-bar-fill" style={{ width: `${pct}%` }} />
                </span>
                <span className={`task-team-pct${assigned ? '' : ' is-none'}`}>
                  {assigned ? `${done} / ${assigned} subtasks` : 'no subtasks'}
                </span>
                <span className={`task-team-state ${row.has_accepted ? 'is-yes' : 'is-no'}`}>
                  {row.has_accepted ? 'Accepted' : 'Not accepted'}
                </span>
              </li>
            );
          }
          const reported = row.progress_percent !== null
            && row.progress_percent !== undefined;
          return (
            <li key={row.id} className="task-team-row">
              <span className="task-team-name" title={row.user_name}>
                {row.user_name}
              </span>
              <span className={`task-bar${reported ? '' : ' is-empty'}`} role="img"
                aria-label={reported
                  ? `${row.user_name}: ${row.progress_percent}% complete`
                  : `${row.user_name}: no progress reported`}>
                <span className="task-bar-fill"
                  style={{ width: `${reported ? row.progress_percent : 0}%` }} />
              </span>
              <span className={`task-team-pct${reported ? '' : ' is-none'}`}>
                {reported ? `${row.progress_percent}%` : 'not reported'}
              </span>
              <span className={`task-team-state ${row.has_accepted ? 'is-yes' : 'is-no'}`}>
                {row.has_accepted ? 'Accepted' : 'Not accepted'}
              </span>
            </li>
          );
        })}

        <li className="task-team-row is-total">
          <span className="task-team-name"><b>Overall</b></span>
          <span className="task-bar task-bar-lg" role="img"
            aria-label={`Overall: ${overallPct}% complete`}>
            <span className="task-bar-fill" style={{ width: `${overallPct}%` }} />
          </span>
          <span className="task-team-pct"><b>{overallPct}%</b></span>
          {/* The mean of what each person who HAS reported says — said in words
              so nobody has to work out why it is not the sum. With subtasks it
              is the plain count, which needs no explaining. */}
          <span className="task-team-state is-note">
            {bySubtasks ? `${totalDone} / ${subtasks.length} subtasks` : 'average of reports'}
          </span>
        </li>
      </ul>
    </section>
  );
};

export default TeamProgress;
