/**
 * NOT MOUNTED ON HOME (Phase MOBILE-DASHBOARD-V3).
 *
 * This card was removed from the Home dashboard, not deleted. Nothing was lost
 * by removing it: an appraisal step that is genuinely your turn already arrives
 * as a My Day row from the `needs_me` scope, and the module's own notifications
 * carry the rest — so on Home it was a second rendering of one fact, and on a
 * phone it was a third of a screen spent on a module most people touch twice a
 * year. The ways in are the People & Attendance overview, the appraisal rail
 * and search; navConfig's reachability table records them.
 *
 * It is kept because it is the right shape for the People overview, which is
 * where the specification points employees. Its tests still run against it
 * directly. If it is still unmounted a phase from now, delete it.
 */

import React from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ClipboardCheck, GraduationCap, Target, Users } from 'lucide-react';

import { useAuth } from '../../hooks/useAuth';
import { appraisalService } from '../../services/appraisalService';
import { EMPTY } from '../../services/emptyStates';

/**
 * The appraisal card on Home (Phase APM-03b).
 *
 * ROLE-AWARE, BUT THE SERVER DECIDES THE ROLE
 * -------------------------------------------
 * Which blocks appear is decided by which KEYS the dashboard payload carries,
 * not by reading the caller's role here. `/appraisals/dashboard/` returns the
 * employee block to everybody, adds the manager block for a supervisor, and
 * adds the HR block for HR and Admin — so this component renders what it was
 * given. Re-deriving the role client-side is how a widget ends up asking for a
 * number the server never sent and showing a permanent dash.
 *
 * ADDITIVE, NOT SUBSTITUTED
 * -------------------------
 * A supervisor sees their OWN appraisal and their team's, because a manager is
 * also somebody with a manager. The same rule the task widgets follow.
 *
 * SILENT ON FAILURE, AND SILENT WHEN THERE IS NOTHING
 * ---------------------------------------------------
 * Home aggregates half a dozen sources; one that will not load must cost its
 * own card and nothing else. And a person with no open appraisal and no team
 * sees NO card rather than an empty one — "Appraisal: —" on the home page every
 * day of the nine months between cycles is noise that teaches people to ignore
 * the whole region.
 */
const Tile = ({ label, value, to, icon, tone = 'neutral', hint }) => (
  <Link to={to} className={`memo-tile tone-${tone}`}>
    <span className="memo-tile-ico" aria-hidden="true">{icon}</span>
    <span className="memo-tile-value">{value ?? '—'}</span>
    <span className="memo-tile-label">{label}</span>
    {hint && <span className="memo-tile-hint">{hint}</span>}
  </Link>
);

const AppraisalCard = () => {
  const { user } = useAuth();

  const { data, isLoading, isError } = useQuery({
    queryKey: ['appraisal', 'dashboard'],
    queryFn: appraisalService.getDashboard,
    staleTime: 60_000,
    retry: false,
    enabled: Boolean(user),
  });

  if (isLoading || isError || !data) return null;

  const me = data.employee || {};
  const manager = data.manager;
  const hr = data.hr;

  // Nothing open, no team, no organisation view: no card.
  if (!me.current_appraisal && !manager && !hr) return null;

  const goals = me.goals || [];
  const activeGoals = goals.length;


  return (
    <section className="hm-appraisal" aria-label="Appraisal">
      <div className="memo-dash-head">
        <h3>Appraisal</h3>
        <Link className="memo-dash-more" to="/appraisals">
          My appraisal →
        </Link>
      </div>

      {me.current_appraisal ? (
        <div className="memo-tiles">
          {/* The stage, in words. "In progress" is a state everybody's
              appraisal is in for most of the year and tells nobody anything;
              "Self Assessment" tells them whose turn it is. */}
          {/* FOUR tiles, not six (Phase APM-UX). A home-page card is read in
              about two seconds; six figures is a dashboard, and a dashboard is
              the thing this module was accused of being. The step name replaces
              the raw stage — "Manager Review" tells somebody whose turn it is,
              "stage 5 of 10" asks them to learn a ladder. */}
          {/* Three cards, not four (Phase DASHBOARD-V1). Progress moved to the
              appraisal page: it is a figure somebody studies once a quarter,
              not one they act on from Home. What stays is the step, the goals,
              and whether it is their turn. */}
          {/* Cycle only — the STEP is deliberately not repeated here.
              With the step as the value or the hint, this card read "My Review
              / My Review" for anybody actually at that step, which is one of
              the five and the likeliest. The third card already says whose turn
              it is, which is the part somebody acts on; the step itself belongs
              on the appraisal page, one click away. */}
          <Tile label="My Review" value={me.cycle}
            to="/appraisals" icon={<Target size={18} />} tone="info" />
          <Tile label="Goals" value={activeGoals} to="/appraisals/goals"
            icon={<Target size={18} />}
            tone={me.goal_weight_total === 100 ? 'neutral' : 'warn'}
            hint={me.goal_weight_total === 100
              ? undefined : `adds up to ${me.goal_weight_total ?? 0}%`} />
          <Tile label={me.awaiting_me ? 'Review Due' : 'With Your Manager'}
            value={me.awaiting_me ? 'Now' : '—'}
            to="/appraisals" icon={<ClipboardCheck size={18} />}
            tone={me.awaiting_me ? 'urgent' : 'ok'}
            hint={me.awaiting_me ? 'Your turn to write' : 'Nothing needed yet'} />
        </div>
      ) : (
        <p className="lr-page-sub">
          {EMPTY.noAppraisalOpen}
        </p>
      )}

      {manager && (
        <div className="memo-tiles">
          <Tile label="Appraisals To Review" value={manager.pending_reviews}
            to="/appraisals/team" icon={<Users size={18} />} tone="warn"
            hint="Appraisals at a stage you own" />
          <Tile label="Team Appraisals" value={manager.team_size}
            to="/appraisals/team" icon={<Users size={18} />} tone="info" />
          <Tile label="Team Completion"
            value={`${manager.completion_percent ?? 0}%`}
            to="/appraisals/team" icon={<ClipboardCheck size={18} />} tone="ok"
            hint={`${manager.completed} of ${manager.team_size} closed`} />
        </div>
      )}

      {hr && (
        <div className="memo-tiles">
          <Tile label="Cycle Completion"
            value={`${hr.cycle_completion?.percent ?? 0}%`} to="/appraisals/hr"
            icon={<ClipboardCheck size={18} />} tone="ok"
            hint={`${hr.cycle_completion?.closed ?? 0} of ${hr.cycle_completion?.total ?? 0} closed`} />
          <Tile label="In Progress" value={hr.cycle_completion?.in_progress}
            to="/appraisals/hr" icon={<Users size={18} />} tone="info" />
          <Tile label="Training Requests" value={hr.training_needs?.total}
            to="/appraisals/hr" icon={<GraduationCap size={18} />} tone="warn" />
        </div>
      )}
    </section>
  );
};

export default AppraisalCard;
