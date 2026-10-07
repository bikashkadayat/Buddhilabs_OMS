import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ArrowRight, FileText, Plus } from 'lucide-react';
import { useAuth } from '../../hooks/useAuth';
import { memoService } from '../../services/memoService';
import { can } from '../../services/roles';

const Tile = ({ label, value, onClick, urgent }) => (
  <button type="button" onClick={onClick} className={`memo-card-tile ${urgent ? 'is-urgent' : ''}`}>
    <span className="memo-card-tile-value">{value ?? '—'}</span>
    <span className="memo-card-tile-label">{label}</span>
  </button>
);

/**
 * Compact memo summary for the main app dashboard.
 *
 * Counts come from GET /memos/dashboard/ rather than from counting rows inside a
 * paginated list response, which is what this card used to do — so its figures
 * were silently capped at one page (PAGE_SIZE = 50) for anyone with a real
 * volume of memos.
 */
const DashboardMemoCard = () => {
  const navigate = useNavigate();
  const { role } = useAuth();
  const { data: counts } = useQuery({
    queryKey: ['memos', 'dashboard'],
    queryFn: memoService.getDashboard,
    refetchInterval: 60_000,
  });

  return (
    <div className="table-card memo-card">
      <div className="memo-card-head">
        <h3><FileText size={18} aria-hidden="true" /> Memos</h3>
        <button type="button" className="memo-card-link" onClick={() => navigate('/memos')}>
          Memo dashboard <ArrowRight size={14} />
        </button>
      </div>

      <div className="memo-card-tiles">
        <Tile label="Pending my action" value={counts?.pending_actions}
          urgent={Boolean(counts?.pending_actions)} onClick={() => navigate('/memos/pending')} />
        <Tile label="Inbox" value={counts?.inbox} onClick={() => navigate('/memos/inbox')} />
        {can(role, 'myMemos') && (
          <Tile label="My drafts" value={counts?.drafts} onClick={() => navigate('/memos/drafts')} />
        )}
        {can(role, 'myMemos') && (
          <Tile label="In flight" value={counts?.outbox} onClick={() => navigate('/memos/outbox')} />
        )}
      </div>

      {can(role, 'createMemo') && (
        <button type="button" className="lr-btn lr-btn-primary" onClick={() => navigate('/memos/create')}>
          <Plus size={14} /> Create Memo
        </button>
      )}
    </div>
  );
};

export default DashboardMemoCard;
