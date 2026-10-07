import React from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Archive, ArrowRight, Building2, CheckCircle2, ClipboardCheck, FileEdit, Plus,
  StickyNote, ThumbsUp, Eye,
} from 'lucide-react';
import { useAuth } from '../../hooks/useAuth';
import { memoService } from '../../services/memoService';
import { can } from '../../services/roles';
import MemoTable from '../../components/memo/MemoTable';
import ChartFrame from '../../components/analytics/ChartFrame';
import ComparisonBarChart from '../../components/analytics/ComparisonBarChart';
import TrendChart from '../../components/analytics/TrendChart';
import { STATUS_COLORS } from '../../components/memo/memoLabels';
import { seriesColour } from '../../components/analytics/chartTheme';
import { Skeleton, ErrorState } from '../../components/leave-records/States';
import { EMPTY } from '../../services/emptyStates';

/**
 * A dashboard stat tile.
 *
 * `tone` drives only the accent colour, never the meaning — the number and label
 * carry that, so the tiles stay readable without colour. `icon` takes a rendered
 * element rather than a component so the caller owns its size in one place.
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
 * Memo module dashboard.
 *
 * Counts come from GET /memos/dashboard/, which runs COUNT queries over the
 * caller's visible set. The original dashboard card counted rows inside a
 * paginated list response instead, so every figure was capped at one page.
 *
 * The three charts reuse this project's existing analytics chart system
 * (ChartFrame + chartTheme), whose palette was already validated for contrast and
 * colour-vision separation. Introducing a second palette here would have given
 * the app two chart languages within one release, which is exactly what
 * chartTheme was written to prevent. ChartFrame is not optional in this codebase:
 * it supplies the "View data" table that makes every chart readable without
 * relying on colour.
 */
const MemoDashboard = () => {
  const navigate = useNavigate();
  const { user, role } = useAuth();

  const { data: counts, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['memos', 'dashboard'],
    queryFn: memoService.getDashboard,
    refetchInterval: 60_000,
  });

  const { data: charts, isLoading: chartsLoading } = useQuery({
    queryKey: ['memos', 'dashboard-charts'],
    queryFn: () => memoService.getDashboardCharts(),
    staleTime: 5 * 60_000,
  });

  const { data: pending } = useQuery({
    queryKey: ['memos', 'dashboard-pending'],
    queryFn: () => memoService.listMemos({ scope: 'pending' }, 1),
  });

  if (isLoading) return <div className="page"><Skeleton rows={4} /></div>;
  if (isError) return <div className="page"><ErrorState error={error} onRetry={refetch} /></div>;

  const pendingItems = (pending?.items ?? []).slice(0, 6);
  const canCreate = can(role, 'createMemo');

  const byDepartment = charts?.by_department ?? [];
  const byStatus = charts?.by_status ?? [];
  const trend = charts?.monthly_trend ?? [];
  const trendTotal = trend.reduce((sum, row) => sum + (row.total || 0), 0);

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h2>Memo Dashboard</h2>
          <div className="lr-page-sub">
            {user?.full_name ? `${user.full_name} · ` : ''}an overview of everything on your desk
          </div>
        </div>
        {canCreate && (
          <button type="button" className="lr-btn lr-btn-primary" onClick={() => navigate('/memos/create')}>
            <Plus size={14} /> Create Memo
          </button>
        )}
      </div>

      {/*
        Phase 49.5 dashboard rationalisation.

        The Phase 49 audit found the endpoint returning fifteen counts while this
        page rendered seven tiles - so five queues a user could be waiting on had
        working server-side counts and no way to see them. Every tile below is now
        one of the counts the API already computes, and every count the brief names
        has a tile: nothing is calculated on each dashboard load and then discarded.

        The four "pending" tiles count only the steps of their own role type, so
        they add up rather than overlap, and "Assigned" is their sum - shown
        because "what is on my desk" is the question people open this page to
        answer, and reading four tiles to add them up is worse than reading one.

        "Pending Note" is deliberately toned neutral even when non-zero: a note is
        informational and does not block anything, so colouring it like an overdue
        approval would misreport what it means.
      */}
      {/* The manual's four tiles, with its own definitions (p.2). The rest of the
          module's counters are real and useful to a secretary, so they sit below
          rather than competing with the four the document names. */}
      <div className="memo-tiles">
        <Tile label="Draft Memo" value={counts.drafts} to="/memos/drafts"
          icon={<FileEdit size={17} />}
          hint="Saved as a draft by you" />
        <Tile label="Assigned Memo" value={counts.assigned} to="/memos/pending"
          icon={<ClipboardCheck size={17} />}
          tone={counts.assigned ? 'urgent' : 'neutral'}
          hint="Assigned to you" />
        <Tile label="Under Process Memo (Initiated)"
          value={counts.under_process_initiated} to="/memos/outbox"
          icon={<Eye size={17} />}
          hint="Raised by you and in the process of approval" />
        <Tile label="Under Process Memo (Involvement)"
          value={counts.under_process_involvement} to="/memos/inbox"
          icon={<ThumbsUp size={17} />}
          hint="You have supported, reviewed, approved or noted it" />
      </div>

      <div className="memo-tiles">
        <Tile label="Pending Review" value={counts.pending_review} to="/memos/pending"
          icon={<Eye size={17} />} tone={counts.pending_review ? 'urgent' : 'neutral'} />
        <Tile label="Pending Recommendation" value={counts.pending_recommendation}
          to="/memos/pending" icon={<ThumbsUp size={17} />}
          tone={counts.pending_recommendation ? 'urgent' : 'neutral'} />
        <Tile label="Pending Support" value={counts.pending_support} to="/memos/pending"
          icon={<ThumbsUp size={17} />}
          tone={counts.pending_support ? 'urgent' : 'neutral'} />
        <Tile label="Pending Approval" value={counts.pending_approval}
          to="/memos/pending" icon={<ClipboardCheck size={17} />}
          tone={counts.pending_approval ? 'urgent' : 'neutral'} />
        <Tile label="Pending Note" value={counts.pending_notes} to="/memos/inbox"
          icon={<StickyNote size={17} />} />
        <Tile label="Department Memo" value={counts.department} to="/memos/department"
          icon={<Building2 size={17} />} />
        <Tile label="Approved" value={counts.approved} to="/memos/approved"
          icon={<CheckCircle2 size={17} />} tone="ok" />
        <Tile label="Archived Memo" value={counts.archived} to="/memos/archived"
          icon={<Archive size={17} />} />
      </div>

      <div className="memo-chart-grid">
        <ChartFrame
          title="Memo by Department"
          subtitle="Total memos raised, by the department that raised them"
          height={280}
          isEmpty={!chartsLoading && byDepartment.length === 0}
          empty="No memos to chart yet."
          rows={byDepartment}
          columns={[{ key: 'label', label: 'Department' }, { key: 'total', label: 'Memos' }]}
        >
          <ComparisonBarChart
            data={byDepartment}
            bars={[{ key: 'total', label: 'Memos', colour: seriesColour(0) }]}
            // Horizontal: department names under a vertical bar chart either
            // rotate to 45 degrees or truncate, and both are worse than turning
            // the chart on its side.
            layout="horizontal"
            labelKey="label"
            height={280}
            // Colour by stable position, so filtering or re-sorting never
            // repaints the surviving departments.
            colourFor={(_row, index) => seriesColour(index)}
          />
        </ChartFrame>

        <ChartFrame
          title="Memo by Status"
          subtitle="Where memos currently sit in the workflow"
          height={280}
          isEmpty={!chartsLoading && byStatus.every((row) => !row.total)}
          empty="No memos to chart yet."
          rows={byStatus}
          columns={[{ key: 'label', label: 'Status' }, { key: 'total', label: 'Memos' }]}
          note="Stages with no memos are shown as zero rather than omitted, so the
                full pipeline stays visible."
        >
          <ComparisonBarChart
            data={byStatus}
            bars={[{ key: 'total', label: 'Memos', colour: seriesColour(0) }]}
            // Vertical and in workflow order: status is ordinal here, so the
            // chart reads as a funnel. A pie would hide that entirely.
            layout="vertical"
            labelKey="label"
            height={280}
            // Each bar wears its own status colour - the same one the badges use,
            // so a status looks the same on the chart as it does in a list.
            colourFor={(row) => (STATUS_COLORS[row.status] || [])[1] || '#64748b'}
          />
        </ChartFrame>

        <ChartFrame
          title="Monthly Memo Trend"
          subtitle="Memos created per month"
          height={280}
          isEmpty={!chartsLoading && trendTotal === 0}
          empty="No memos created in this window."
          rows={trend}
          columns={[{ key: 'label', label: 'Month' }, { key: 'total', label: 'Memos' }]}
        >
          <TrendChart
            data={trend}
            series={[{ key: 'total', label: 'Memos created', colour: seriesColour(0) }]}
            area
            height={280}
            xKey="label"
          />
        </ChartFrame>
      </div>

      <section className="memo-dash-section">
        <div className="memo-dash-head">
          <h3>Waiting on you</h3>
          <Link to="/memos/pending" className="memo-dash-more">View all <ArrowRight size={13} /></Link>
        </div>
        {pendingItems.length === 0 ? (
          <p className="lr-page-sub">{EMPTY.nothingWaiting}</p>
        ) : (
          <MemoTable
            items={pendingItems}
            columns={['type', 'author', 'pendingSince', 'dueDays', 'action']}
          />
        )}
      </section>
    </div>
  );
};

export default MemoDashboard;
