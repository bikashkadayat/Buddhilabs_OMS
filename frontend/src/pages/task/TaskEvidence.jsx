import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, FileCheck, Info } from 'lucide-react';

import { taskService } from '../../services/taskService';
import { Skeleton, ErrorState } from '../../components/leave-records/States';

/**
 * The task evidence page (Phase T5.7 / T5.8).
 *
 * WHAT THIS PAGE IS FOR
 * ---------------------
 * It shows a person what their own task record contains, in the shape a future
 * appraisal will read it. Somebody should be able to see the evidence about
 * themselves BEFORE it is used in a conversation about them — a record you
 * cannot inspect is one you cannot correct.
 *
 * WHAT IT DELIBERATELY DOES NOT DO
 * --------------------------------
 * No score. No rating. No comparison to a colleague, a team average or a
 * target. Every figure is a count or a duration reported beside the number it
 * was calculated over, and the page says so in as many words. Phase T5's
 * instruction is to build the evidence service and not appraisal, and a page
 * that quietly implied a verdict would have crossed that line whatever the API
 * returned.
 *
 * The snapshot history is here for the same reason: it is what the data said on
 * each day, so a figure quoted months later can be checked against the day it
 * was true rather than recomputed against tasks that have since moved.
 */
const Figure = ({ label, value, basis, hint }) => (
  <span className="memo-tile tone-neutral">
    <span className="memo-tile-value">
      {value === null || value === undefined ? '—' : value}
    </span>
    <span className="memo-tile-label">{label}</span>
    {/* The denominator is rendered, always. A percentage without one is the
        kind of number that gets quoted for years. Passed as a whole phrase
        rather than prefixed here — "of 9 of 12 tasks" is what happens when a
        component decides the wording for a caller that already knows it. */}
    {basis && <span className="memo-tile-hint">{basis}</span>}
    {hint && <span className="memo-tile-hint">{hint}</span>}
  </span>
);

const TaskEvidence = () => {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['tasks', 'evidence'],
    queryFn: () => taskService.getEvidence(),
  });
  const snapshots = useQuery({
    queryKey: ['tasks', 'evidence', 'snapshots'],
    queryFn: () => taskService.getEvidenceSnapshots(),
    retry: false,
  });

  if (isLoading) return <div className="page"><Skeleton rows={4} /></div>;
  if (isError) {
    return <div className="page"><ErrorState error={error} onRetry={refetch} /></div>;
  }

  const rows = snapshots.data?.snapshots || [];

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">My Task Performance</h1>
          <p className="lr-page-sub">
            {data.employee_name} · {data.period_start} to {data.period_end}
          </p>
        </div>
      </div>

      {/* Stated at the top, not buried. Somebody reading their own record
          deserves to know what it is and is not before they read the numbers. */}
      <div className="task-callout is-warn" role="note">
        <Info size={13} aria-hidden="true" /> <b>This is a record of activity,
        not an assessment.</b> {data.disclaimer}
      </div>

      {data.low_volume && (
        /* The fairness caveat, shown to the PERSON as well as to any manager.
           Percentages over a handful of tasks are noise, and somebody should
           know that about their own numbers before anybody quotes them. */
        <div className="task-callout is-no" role="note">
          <AlertTriangle size={13} aria-hidden="true" /> <b>Too few tasks to
          read as a pattern.</b> With {data.tasks_assigned} task
          {data.tasks_assigned === 1 ? '' : 's'} in this period, the percentages
          below are not a meaningful measure of anything.
        </div>
      )}

      <section className="memo-dash-section">
        <div className="memo-dash-head"><h3>Completion</h3></div>
        <div className="memo-tiles">
          <Figure label="Assigned" value={data.tasks_assigned} />
          <Figure label="Completed" value={data.tasks_completed} />
          <Figure label="Open" value={data.tasks_open} />
          <Figure label="Overdue" value={data.tasks_overdue} />
          <Figure label="Completion" value={`${data.completion_percent}%`}
            basis={`${data.tasks_completed} of ${data.completion_of} tasks`} />
        </div>
      </section>

      <section className="memo-dash-section">
        <div className="memo-dash-head"><h3>Timeliness</h3></div>
        <div className="memo-tiles">
          <Figure label="On Time" value={`${data.on_time_percent}%`}
            basis={`${data.completed_on_time} of ${data.completed_with_due_date} with a due date`}
            hint="Tasks with no due date are not counted" />
          <Figure label="Avg Completion" value={data.average_completion_days}
            hint="Days from assignment. Blank means nothing completed yet." />
          <Figure label="Overdue" value={data.tasks_overdue}
            hint="Still open and past their date" />
        </div>
      </section>

      <section className="memo-dash-section">
        <div className="memo-dash-head"><h3>Reviews</h3></div>
        <div className="memo-tiles">
          <Figure label="Reviews Performed" value={data.reviews_performed}
            hint="Decisions you made, not decisions about your work" />
        </div>
      </section>

      <section className="memo-dash-section">
        <div className="memo-dash-head"><h3>Evidence</h3></div>
        <div className="memo-tiles">
          <Figure label="Checklist Items"
            value={data.checklist_items_completed}
            basis={`of ${data.checklist_items_total} items`} />
          <Figure label="Evidence Files" value={data.evidence_files_submitted} />
          <Figure label="Comments" value={data.comments_posted} />
        </div>
      </section>

      {rows.length > 0 && (
        <section className="memo-dash-section">
          <div className="memo-dash-head">
            <h3>Historical Trends</h3>
            <span className="memo-tile-hint">
              <FileCheck size={12} aria-hidden="true" /> What the data said on
              each day
            </span>
          </div>
          <div className="lr-table-wrap">
            <table className="lr-table">
              <thead>
                <tr>
                  <th scope="col">Date</th>
                  <th scope="col">Assigned</th>
                  <th scope="col">Completed</th>
                  <th scope="col">Open</th>
                  <th scope="col">Overdue</th>
                  <th scope="col">Completion</th>
                  <th scope="col">On Time</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.snapshot_date}>
                    <td style={{ whiteSpace: 'nowrap' }}>{row.snapshot_date}</td>
                    <td>{row.tasks_assigned}</td>
                    <td>{row.tasks_completed}</td>
                    <td>{row.tasks_open}</td>
                    <td className={row.tasks_overdue > 0 ? 'task-late' : undefined}>
                      {row.tasks_overdue}
                    </td>
                    <td>{row.completion_percent}%</td>
                    <td>{row.on_time_percent}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
};

export default TaskEvidence;
