import React from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Archive, BadgeCheck, ClipboardCheck, Eye, FileEdit, MailOpen, Megaphone,
  PenLine, Plus, Send, Stamp,
} from 'lucide-react';

import { circularService } from '../../services/circularService';
import { Skeleton, ErrorState } from '../../components/leave-records/States';

/**
 * A dashboard tile. `tone` drives only the accent; the number and the label carry
 * the meaning, so the tiles stay readable without colour.
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
 * Circular dashboard (Phase 50).
 *
 * Every count comes from GET /circulars/dashboard/, which runs COUNT queries over
 * the caller's own visible set - so a tile can never advertise a circular the user
 * cannot open, and no total is a row count from a paginated response.
 *
 * The tiles are grouped into three sections because they answer three different
 * questions, and mixing them is what made another module's dashboard grow to
 * twenty-three tiles nobody could scan:
 *
 *   Pipeline  - where circulars I am responsible for have got to.
 *   On my desk - what is waiting for ME to do something about.
 *   Circulation - what is out there and how it is landing.
 */
const CircularDashboard = () => {
  const navigate = useNavigate();

  const { data: counts, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['circulars', 'dashboard'],
    queryFn: circularService.getDashboard,
  });

  if (isLoading) return <div className="page"><Skeleton rows={4} /></div>;
  if (isError) {
    return <div className="page"><ErrorState error={error} onRetry={refetch} /></div>;
  }

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">Circular Dashboard</h1>
          <p className="lr-page-sub">
            Policy communication, administrative notices and official instructions
          </p>
        </div>
        <button type="button" className="lr-btn lr-btn-primary"
          onClick={() => navigate('/circulars/create')}>
          <Plus size={14} /> Create Circular
        </button>
      </div>

      <h2 className="memo-section-head">Pipeline</h2>
      <div className="memo-tiles" data-testid="circular-pipeline-tiles">
        <Tile label="Draft" value={counts.drafts} to="/circulars/drafts"
          icon={<FileEdit size={18} />} tone="neutral"
          hint="Yours, plus any returned for revision" />
        <Tile label="Under Review" value={counts.under_review}
          to="/circulars/under-review" icon={<Eye size={18} />} tone="info" />
        <Tile label="Ready For Issue" value={counts.ready_for_issue}
          to="/circulars/ready-for-issue" icon={<PenLine size={18} />} tone="warn"
          hint="Reviewed, awaiting a signature" />
        <Tile label="Ready For Broadcast" value={counts.ready_for_broadcast}
          to="/circulars/ready-for-broadcast" icon={<Send size={18} />} tone="warn"
          hint="Official, awaiting an audience" />
      </div>

      <h2 className="memo-section-head">On My Desk</h2>
      <div className="memo-tiles" data-testid="circular-desk-tiles">
        <Tile label="Assigned" value={counts.assigned} to="/circulars/assigned"
          icon={<ClipboardCheck size={18} />}
          tone={counts.assigned ? 'warn' : 'neutral'}
          hint="Waiting on your review or signature" />
        <Tile label="Unread" value={counts.unread} to="/circulars/unread"
          icon={<MailOpen size={18} />} tone={counts.unread ? 'info' : 'neutral'}
          hint="Sent to you and not yet opened" />
        <Tile label="Pending Acknowledgement" value={counts.pending_acknowledgement}
          to="/circulars/my-acknowledgements" icon={<Stamp size={18} />}
          tone={counts.pending_acknowledgement ? 'warn' : 'neutral'}
          hint="Awaiting your confirmation" />
        <Tile label="Acknowledged" value={counts.acknowledged}
          to="/circulars/my-acknowledgements" icon={<BadgeCheck size={18} />}
          tone="ok" hint="Confirmed by you" />
      </div>

      <h2 className="memo-section-head">Circulation</h2>
      <p className="lr-page-sub">
        Opening a circular is recorded automatically. It is not the same as
        acknowledging one, and only some circulars ask for that.
      </p>
      <div className="memo-tiles" data-testid="circular-circulation-tiles">
        <Tile label="Broadcasted" value={counts.broadcasted}
          to="/circulars/broadcasted" icon={<Megaphone size={18} />} tone="ok"
          hint="In circulation now" />
        <Tile label="Archived" value={counts.archived} to="/circulars/archived"
          icon={<Archive size={18} />} tone="ok"
          hint="Filed to the permanent record" />
      </div>
    </div>
  );
};

export default CircularDashboard;
