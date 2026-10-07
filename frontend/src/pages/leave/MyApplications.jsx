import React, { useEffect, useState } from 'react';
import BrandLogo from '../../components/branding/BrandLogo';
import { useNavigate } from 'react-router-dom';
import { useLeaves } from '../../hooks/useLeaves';
import { useAutoRefresh } from '../../hooks/useAutoRefresh';
import { useAuth } from '../../hooks/useAuth';
import Badge from '../../components/common/Badge';
import LeaveDocActions from '../../components/documents/LeaveDocActions';
import { ArrowLeft } from 'lucide-react';
import EmptyState from '../../components/common/EmptyState';
import { EMPTY } from '../../services/emptyStates';

const MyApplications = () => {
  const navigate = useNavigate();
  const { leaves, loading, error, fetchLeaves } = useLeaves();
  const { user, role } = useAuth();
  const [filter, setFilter] = useState('all');

  useEffect(() => {
    fetchLeaves();
  }, [fetchLeaves]);
  useAutoRefresh(fetchLeaves, 20000); // keep the list fresh (poll + focus)

  // Prevent approvers from accessing this page
  if (role === 'approver') {
    return (
      <div className="page">
        <div className="pg-head">
          <div className="pg-head-left">
            <div className="pg-breadcrumb">
              <button className="pg-back" aria-label="Back" onClick={() => navigate(-1)}>
                <ArrowLeft size={18} />
              </button>
              Leave Management
            </div>
            <div className="pg-title">Access Denied</div>
            <div className="pg-desc">Approvers cannot view their own applications.</div>
          </div>
          <div className="pg-head-right">
            <div className="pg-logo">
              <BrandLogo variant="letterhead" />
            </div>
          </div>
        </div>
        <EmptyState variant="denied" title={EMPTY.noPermission} />
      </div>
    );
  }

  // Show only the current user's applications. Match on owner id (robust);
  // fall back to name only if the id isn't present in the payload.
  const mine = leaves.filter(l => (l.userId ? l.userId === user.id : l.employee === user.name));
  const myLeaves = mine.filter(l => filter === 'all' || l.status === filter);

  return (
    <div className="page">
      <div className="pg-head">
        <div className="pg-head-left">
          <div className="pg-breadcrumb">
            <button className="pg-back" aria-label="Back" onClick={() => navigate('/leave')}>
              <ArrowLeft size={18} />
            </button>
            Leave Management
          </div>
          <div className="pg-title">My Applications</div>
          <div className="pg-desc">Track all your leave applications</div>
        </div>
        <div className="pg-head-right">
          <div className="pg-logo">
            <BrandLogo variant="letterhead" />
          </div>
        </div>
      </div>
      <div className="table-card">
        <div className="tc-top">
          <span className="tc-title">Applications</span>
          <div className="filter-row">
            <button className={`ftag ${filter === 'all' ? 'on' : ''}`} onClick={() => setFilter('all')}>All</button>
            <button className={`ftag ${filter === 'pending' ? 'on' : ''}`} onClick={() => setFilter('pending')}>Pending</button>
            <button className={`ftag ${filter === 'approved' ? 'on' : ''}`} onClick={() => setFilter('approved')}>Approved</button>
            <button className={`ftag ${filter === 'rejected' ? 'on' : ''}`} onClick={() => setFilter('rejected')}>Rejected</button>
          </div>
        </div>

        {loading ? (
          <div style={{ padding: '24px', textAlign: 'center' }}>Loading applications...</div>
        ) : error ? (
          <div style={{ padding: '32px', textAlign: 'center' }}>
            <div style={{ color: 'var(--danger, #b91c1c)', marginBottom: 12 }}>Could not load your applications. {error}</div>
            <button className="btn btn-primary" onClick={fetchLeaves}>Retry</button>
          </div>
        ) : (
          <div className="resp-table-wrap">
            <table className="resp-table">
              <thead>
                <tr>
                  <th>Reference</th>
                  <th>Leave Details</th>
                  <th>Dates</th>
                  <th>Status</th>
                  <th>Documents</th>
                </tr>
              </thead>
              <tbody>
                {myLeaves.map(leave => (
                  <tr key={leave.id} className="mrow">
                    <td data-label="Reference">
                      <div className="ref-no">{leave.id}</div>
                      <div className="leave-meta">{leave.applied}</div>
                    </td>
                    <td data-label="Leave Details">
                      <div className="leave-title">{leave.type}</div>
                      <div className="leave-meta">{leave.reason}</div>
                    </td>
                    <td data-label="Dates">
                      <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-primary)' }}>{leave.start} to {leave.end}</div>
                      <div className="leave-meta">{leave.days} Days</div>
                    </td>
                    <td data-label="Status">
                      <Badge status={leave.status} />
                    </td>
                    <td data-label="Documents">
                      {leave.status === 'approved'
                        ? <LeaveDocActions leaveId={leave.id} />
                        : <span style={{ color: 'var(--text-muted)', fontSize: 'var(--fs-meta)' }}>Available when approved</span>}
                    </td>
                  </tr>
                ))}
                {myLeaves.length === 0 && (
                  <tr>
                    <td colSpan="5" style={{ textAlign: 'center', padding: '60px', color: 'var(--text-muted)' }}>
                      You haven’t applied for leave yet.{' '}
                      <button type="button" onClick={() => navigate('/leave/apply')}
                              style={{ background: 'none', border: 'none', color: 'var(--brand-blue)', fontWeight: 600, cursor: 'pointer' }}>
                        Apply for leave
                      </button>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};

export default MyApplications;
