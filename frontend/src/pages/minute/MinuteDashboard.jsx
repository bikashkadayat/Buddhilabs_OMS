import React from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Archive, CheckCircle2, ClipboardList, FileEdit, Plus, UserCheck,
} from 'lucide-react';

import { minuteService } from '../../services/minuteService';
import ChartFrame from '../../components/analytics/ChartFrame';
import ComparisonBarChart from '../../components/analytics/ComparisonBarChart';
import { seriesColour } from '../../components/analytics/chartTheme';
import MoreDetails from '../../components/minute/MoreDetails';
import { Skeleton, ErrorState } from '../../components/leave-records/States';

/**
 * A dashboard tile. `tone` drives only the accent; the number and label carry the
 * meaning, so the tiles stay readable without colour.
 */
const Tile = ({ label, value, to, icon, tone = 'neutral', hint }) => (
  <Link to={to} className={`memo-tile tone-${tone}`}>
    <span className="memo-tile-ico" aria-hidden="true">{icon}</span>
    <span className="memo-tile-value">{value ?? '—'}</span>
    <span className="memo-tile-label">{label}</span>
    {hint && <span className="memo-tile-hint">{hint}</span>}
  </Link>
);

/**
 * Minute module dashboard.
 *
 * The manual's own dashboard carries Draft Minute, Assigned Minute and Underprocess
 * Minute counters (p.3). Those are here, alongside the two this HRMS's sidebar is
 * built on - Needs My Action and My Minutes - and the archive. The charts sit behind a
 * disclosure: they are useful to a secretary, not to somebody opening the page to see
 * whether anything is waiting on them.
 *
 * Counts come from GET /minutes/dashboard/, which runs COUNT queries over the caller's
 * OWN visible set, so a tile can never advertise a minute the user cannot open.
 */
const MinuteDashboard = () => {
  const navigate = useNavigate();

  const { data: counts, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['minutes', 'dashboard'],
    queryFn: minuteService.getDashboard,
  });

  const { data: charts } = useQuery({
    queryKey: ['minutes', 'dashboard', 'charts'],
    queryFn: minuteService.getCharts,
  });

  if (isLoading) return <div className="page"><Skeleton rows={4} /></div>;
  if (isError) {
    return <div className="page"><ErrorState error={error} onRetry={refetch} /></div>;
  }

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">Minutes</h1>
          <p className="lr-page-sub">
            Meeting minutes, and who still has to acknowledge them
          </p>
        </div>
        <button type="button" className="lr-btn lr-btn-primary"
          onClick={() => navigate('/minutes/create')}>
          <Plus size={14} /> Create Minute
        </button>
      </div>

      <div className="memo-tiles">
        <Tile label="Needs My Action" value={counts?.needs_my_action}
          to="/minutes/needs-me" icon={<ClipboardList size={18} />} tone="warn"
          hint="To review as FRO, or to acknowledge" />
        <Tile label="To Acknowledge" value={counts?.my_pending_acknowledgements}
          to="/minutes/my-acknowledgements" icon={<UserCheck size={18} />} tone="warn"
          hint="Meetings you attended" />
        <Tile label="My Minutes" value={counts?.my_minutes} to="/minutes/mine"
          icon={<FileEdit size={18} />} tone="neutral"
          hint="Everything you have written" />
        <Tile label="Draft Minute" value={counts?.my_drafts} to="/minutes/drafts"
          icon={<FileEdit size={18} />} tone="info"
          hint="Not yet submitted" />
        <Tile label="Underprocess Minute" value={counts?.under_process}
          to="/minutes/under-process" icon={<ClipboardList size={18} />} tone="info"
          hint="In review or awaiting acknowledgements" />
        <Tile label="Archived Minutes" value={counts?.archived}
          to="/minutes/archived" icon={<Archive size={18} />} tone="ok"
          hint="Acknowledged by everyone present" />
      </div>

      <MoreDetails title="Figures"
        hint="Minutes by status and by type, across everything you can see">
        <div className="memo-charts">
          <ChartFrame
            title="Minutes by Status" height={280}
            isEmpty={!charts?.by_status?.length}
            rows={charts?.by_status}
            columns={[{ key: 'label', label: 'Status' },
              { key: 'value', label: 'Minutes' }]}>
            <ComparisonBarChart
              data={charts?.by_status || []}
              bars={[{ key: 'value', name: 'Minutes' }]}
              layout="vertical" height={280}
              colourFor={(row, index) => seriesColour(index)} />
          </ChartFrame>

          <ChartFrame
            title="Minutes by Type" height={280}
            isEmpty={!charts?.by_type?.length}
            rows={charts?.by_type}
            columns={[{ key: 'label', label: 'Type' },
              { key: 'value', label: 'Minutes' }]}>
            <ComparisonBarChart
              data={charts?.by_type || []}
              bars={[{ key: 'value', name: 'Minutes' }]}
              layout="horizontal" height={280} />
          </ChartFrame>
        </div>

        <div className="memo-tiles">
          <Tile label="Acknowledged" value={counts?.my_acknowledged}
            to="/minutes/acknowledged" icon={<CheckCircle2 size={18} />} tone="ok"
            hint="Minutes you have confirmed reading" />
          <Tile label="Assigned To Me" value={counts?.assigned_to_me}
            to="/minutes/involvement" icon={<ClipboardList size={18} />} tone="info"
            hint="Routed to you by somebody else" />
          <Tile label="Draft for Review" value={counts?.draft_for_review}
            to="/minutes/draft-for-review" icon={<FileEdit size={18} />} tone="info"
            hint="With you as FRO" />
        </div>
      </MoreDetails>
    </div>
  );
};

export default MinuteDashboard;
