import React from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Info } from 'lucide-react';

import { appraisalService } from '../../services/appraisalService';
import { Skeleton, ErrorState } from '../../components/leave-records/States';

/**
 * My Evidence (Phase APM-03b).
 *
 * GROUPED BY SOURCE, INCLUDING THE SOURCES THAT CANNOT ANSWER
 * -----------------------------------------------------------
 * The evidence registry declares seven sources — task, memo, minute, circular,
 * attendance, leave, inventory — and only ONE is implemented. This page shows
 * all seven, and says plainly which have no provider yet.
 *
 * That is the whole point of rendering the empty ones. Somebody reading an
 * appraisal backed only by task activity needs to know that their memo and
 * minute work is ABSENT rather than ZERO: "no evidence from Leave" and "Leave
 * evidence is not collected on this system" are completely different sentences
 * to have quoted at you in a review, and only one of them is true. A page that
 * quietly listed just the source that works would let the first reading stand.
 *
 * SUGGESTED / USED / UNUSED
 * -------------------------
 * Three states, defined precisely, because the words invite a vaguer reading:
 *
 *   USED      — cited onto the record. Frozen with the date it was read.
 *   SUGGESTED — available from a source that can answer, not yet cited.
 *   UNUSED    — a source that CAN answer but has nothing for this period, or
 *               cannot answer at all. Named, never silently omitted.
 *
 * Citing is deliberately not done from this page. The record is where evidence
 * is attached, against a specific objective, with a reason — one click from
 * here. Evidence offered without a reason is a number somebody dropped into a
 * conversation about their own year, which is exactly what the citation note
 * exists to prevent.
 */
/**
 * The three figures that lead the page.
 *
 * Not a filter on what exists — the rest stay on the page behind a disclosure.
 * These are simply the three a person can hold at a glance: what I finished,
 * what I reviewed for other people, and what I attached to show for it.
 */
const HEADLINE = ['tasks_completed', 'review_participation',
                  'evidence_uploaded'];

const SOURCE_LABELS = {
  task: 'Tasks', memo: 'Memos', minute: 'Minutes', circular: 'Circulars',
  attendance: 'Attendance', leave: 'Leave', inventory: 'Inventory',
};

const Figure = ({ metric }) => (
  <span className="memo-tile tone-neutral">
    <span className="memo-tile-value">
      {metric.value === null || metric.value === undefined
        ? '—' : `${metric.value}${metric.unit === 'percent' ? '%' : ''}`}
    </span>
    <span className="memo-tile-label">{metric.label}</span>
    {metric.basis_of && (
      <span className="memo-tile-hint">{metric.basis_of}</span>
    )}
    {metric.definition && (
      <span className="memo-tile-hint">{metric.definition}</span>
    )}
  </span>
);

const MyEvidence = () => {
  const dash = useQuery({
    queryKey: ['appraisal', 'dashboard'],
    queryFn: appraisalService.getDashboard,
  });
  const id = dash.data?.employee?.current_appraisal;

  const evidence = useQuery({
    queryKey: ['appraisal', id, 'evidence'],
    queryFn: () => appraisalService.getEvidence(id),
    enabled: Boolean(id),
    retry: false,
  });
  const record = useQuery({
    queryKey: ['appraisal', id],
    queryFn: () => appraisalService.getAppraisal(id),
    enabled: Boolean(id),
  });

  if (dash.isLoading) return <div className="page"><Skeleton rows={4} /></div>;
  if (dash.isError) {
    return (
      <div className="page">
        <ErrorState error={dash.error} onRetry={dash.refetch} />
      </div>
    );
  }

  if (!id) {
    return (
      <div className="page memo-page">
        <div className="lr-page-head">
          <div>
            <h1 className="lr-page-title">My Work Record</h1>
            <p className="lr-page-sub">
              You have no appraisal open, so there is no period to collect
              evidence over.
            </p>
          </div>
        </div>
      </div>
    );
  }

  const pack = evidence.data;
  const cited = record.data?.evidence_references || [];
  const citedSources = new Set(cited.map((row) => row.source));
  const sources = pack?.sources || [];

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">My Work Record</h1>
          <p className="lr-page-sub">
            {pack ? `${pack.period_start} to ${pack.period_end}` : 'Loading…'}
          </p>
        </div>
        <Link className="btn btn-ghost btn-sm" to={`/appraisals/${id}`}>
          Add to my appraisal
        </Link>
      </div>

      <div className="task-callout is-warn" role="note">
        <Info size={13} aria-hidden="true" /> <b>This is a record of activity,
        not an assessment.</b> Nothing in your appraisal is scored, weighted or
        computed from these figures — they are context for a conversation with a
        person.
      </div>

      {evidence.isLoading && <Skeleton rows={3} />}
      {evidence.isError && (
        <p className="lr-page-sub">
          Your work record could not be loaded. The rest of your appraisal is
          unaffected.
        </p>
      )}

      {pack?.low_volume && (
        <div className="task-callout is-no" role="note">
          <AlertTriangle size={13} aria-hidden="true" /> <b>Too few records to
          read as a pattern.</b> The percentages below are not a meaningful
          measure over this few, and you are entitled to say so if anybody
          quotes them.
        </div>
      )}

      {sources.map((entry) => {
        const label = SOURCE_LABELS[entry.source] || entry.source;
        const isTask = entry.source === 'task';
        // Only the task source has a provider today, so it is the only one with
        // figures to show. The rest are rendered as what they are.
        const metrics = isTask && pack?.available ? (pack.headline || []) : [];
        const used = cited.filter((row) => row.source === entry.source);

        return (
          <section key={entry.source} className="memo-dash-section"
            aria-labelledby={`ev-${entry.source}-h`}>
            <div className="memo-dash-head">
              <h3 id={`ev-${entry.source}-h`}>{label}</h3>
              <span className="memo-tile-hint">
                {!entry.available ? 'Not currently tracked'
                  : used.length ? `${used.length} item${used.length === 1 ? '' : 's'} added`
                    : 'Not yet added to your appraisal'}
              </span>
            </div>

            {/* "Not currently tracked", never "0 records". An absence rendered
                as a zero reads as "you did none of this" — a claim the system
                has no basis for and the person has no way to correct. */}
            {!entry.available ? (
              <p className="lr-page-sub">
                <b>Not currently tracked.</b> Your {label.toLowerCase()} work is
                not measured by this system yet — this is not a score of zero,
                and nothing in your appraisal should be read as though it were.
              </p>
            ) : metrics.length === 0 ? (
              <p className="lr-page-sub">
                Nothing recorded for this period.
              </p>
            ) : (
              <>
                {/* Three figures lead, because three is what somebody takes in.
                    The other four are one click away and NEVER removed: the
                    employee's own page must show everything a reviewer can see
                    about them, or the person being appraised knows less about
                    their record than the person appraising them. */}
                <div className="memo-tiles">
                  {metrics.filter((m) => HEADLINE.includes(m.key))
                    .map((metric) => (
                      <Figure key={metric.key} metric={metric} />
                    ))}
                </div>
                {metrics.some((m) => !HEADLINE.includes(m.key)) && (
                  <details className="apr-more-figures">
                    <summary>Show all figures</summary>
                    <div className="memo-tiles">
                      {metrics.filter((m) => !HEADLINE.includes(m.key))
                        .map((metric) => (
                          <Figure key={metric.key} metric={metric} />
                        ))}
                    </div>
                  </details>
                )}
                {used.length === 0 && (
                  <p className="lr-page-sub">
                    Not added to your appraisal yet. You choose what to include.
                  </p>
                )}
              </>
            )}

            {used.length > 0 && (
              <>
                <h4 className="apr-sub-h">Added to your appraisal</h4>
                <ul className="apr-cited">
                  {used.map((row) => (
                    <li key={row.id}>
                      {row.period_start} to {row.period_end} · read{' '}
                      {new Date(row.captured_at).toLocaleDateString()} · cited
                      by {row.attached_by_name || '—'}
                      {row.note && (
                        <div className="apr-cited-note">“{row.note}”</div>
                      )}
                      {Array.isArray(row.metrics) && row.metrics.length > 0 && (
                        <div className="apr-cited-note">
                          {row.metrics.map(
                            (m) => `${m.label}: ${m.value ?? '—'}`).join(' · ')}
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </section>
        );
      })}

      {sources.length > 0 && citedSources.size === 0 && (
        <p className="lr-page-sub">
          You have not added anything to your appraisal yet. You add it on your
          appraisal, against a specific goal and with a note saying why — a
          figure offered without one is just a number in a conversation about
          you.
        </p>
      )}
    </div>
  );
};

export default MyEvidence;
