import React, { useEffect, useState } from 'react';
import BrandLogo from '../../components/branding/BrandLogo';
import { useNavigate } from 'react-router-dom';
import { useLeaves } from '../../hooks/useLeaves';
import { useAutoRefresh } from '../../hooks/useAutoRefresh';
import { useAuth } from '../../hooks/useAuth';
import LeaveCard from '../../components/common/LeaveCard';
import DashboardRecordsSummary from '../../components/leave-records/DashboardRecordsSummary';
import DashboardMemoCard from '../../components/memo/DashboardMemoCard';
import DepartmentStats from '../../components/admin/DepartmentStats';
import GovernanceCard from '../../components/admin/GovernanceCard';
import AttendanceWidget from '../../components/attendance/AttendanceWidget';
import LiveAttendancePanel from '../../components/attendance/LiveAttendancePanel';
import CategoryBalances from '../../components/leave/CategoryBalances';
import LeavePolicyWidget from '../../components/leave/LeavePolicyWidget';
import { CalendarDays, Stethoscope, Briefcase, Plane, Plus, ArrowRight, ClipboardCheck, Users, Calendar, FileText, CheckCircle, Clock, XCircle } from 'lucide-react';

const LeaveDashboard = () => {
  const navigate = useNavigate();
  const { leaves, loading, error, fetchLeaves, fetchBalances } = useLeaves();
  const { role, user } = useAuth();
  const [showWelcome, setShowWelcome] = useState(() => {
    const justLoggedIn = localStorage.getItem('justLoggedIn');
    if (justLoggedIn) {
      localStorage.removeItem('justLoggedIn');
      return true;
    }
    return false;
  });

  useEffect(() => {
    if (showWelcome) {
      const timer = setTimeout(() => setShowWelcome(false), 4000);
      return () => clearTimeout(timer);
    }
  }, [showWelcome]);

  useEffect(() => {
    fetchLeaves();
    if (role !== 'approver') {
      fetchBalances();
    }
  }, [fetchLeaves, fetchBalances, role]);

  // Keep counts/lists/balances fresh across accounts (poll + refetch on focus).
  useAutoRefresh(() => { fetchLeaves(); if (role !== 'approver') fetchBalances(); }, 20000);

  const stats = {
    total: leaves.length,
    pending: leaves.filter(l => l.status === 'pending').length,
    approved: leaves.filter(l => l.status === 'approved').length,
    rejected: leaves.filter(l => l.status === 'rejected').length
  };

  const recent = leaves.slice(0, 4);

  if (role === 'checker' || role === 'approver' || role === 'bod' || role === 'admin') {
    return (
      <div className="page" style={{ paddingBottom: '80px' }}>
        {showWelcome && (
          <div className="toast-notification">
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
            <span>Login successful! Welcome, {user?.name || 'User'}</span>
          </div>
        )}

        <div className="pg-head">
          <div className="pg-head-left">
            <div className="pg-breadcrumb">Leave Management</div>
            <div className="pg-title">Approval Dashboard</div>
            <div className="pg-desc">Review and manage team leave applications</div>
          </div>
          <div className="pg-head-right">
            <div className="pg-logo">
              <BrandLogo variant="letterhead" />
            </div>
          </div>
        </div>

        {role !== 'admin' && <AttendanceWidget />}

        {/* Live biometric attendance. Server-side scoping decides what is in
            it, so a department head sees only their own team. */}
        <LiveAttendancePanel />

        <DashboardMemoCard />

        <div className="dash-stat-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '24px', marginBottom: '32px' }}>
          <div className="side-card" style={{ padding: '24px', cursor: 'pointer' }} onClick={() => navigate('/leave/pending')}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
              <div style={{ width: '48px', height: '48px', borderRadius: '12px', background: 'var(--warning-bg)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Clock size={24} color="var(--warning)" />
              </div>
              <div>
                <div style={{ fontSize: 'var(--fs-display)', fontWeight: 700, color: 'var(--warning)', lineHeight: 1 }}>{stats.pending}</div>
                <div style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text-secondary)', marginTop: '4px' }}>Pending Approvals</div>
              </div>
            </div>
          </div>

          <div className="side-card" style={{ padding: '24px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
              <div style={{ width: '48px', height: '48px', borderRadius: '12px', background: 'var(--success-bg)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <CheckCircle size={24} color="var(--success)" />
              </div>
              <div>
                <div style={{ fontSize: 'var(--fs-display)', fontWeight: 700, color: 'var(--success)', lineHeight: 1 }}>{stats.approved}</div>
                <div style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text-secondary)', marginTop: '4px' }}>Approved</div>
              </div>
            </div>
          </div>

          <div className="side-card" style={{ padding: '24px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
              <div style={{ width: '48px', height: '48px', borderRadius: '12px', background: 'var(--danger-bg)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <XCircle size={24} color="var(--danger)" />
              </div>
              <div>
                <div style={{ fontSize: 'var(--fs-display)', fontWeight: 700, color: 'var(--danger)', lineHeight: 1 }}>{stats.rejected}</div>
                <div style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text-secondary)', marginTop: '4px' }}>Rejected</div>
              </div>
            </div>
          </div>
        </div>

        {/* Governance detail lives HERE now, not on Home
            (Phase DASHBOARD-GOVERNANCE-CLEANUP). */}
        {role === 'admin' && <GovernanceCard />}
        {role === 'admin' && <DepartmentStats />}

        <div className="table-card" style={{ padding: '24px' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px' }}>
            <h3 style={{ fontSize: 'var(--fs-h2)', fontWeight: 700, color: 'var(--text-primary)' }}>What You Can Do</h3>
          </div>
          
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '20px' }}>
            <div style={{ padding: '24px', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', transition: 'all 0.2s', cursor: 'pointer' }} onClick={() => navigate('/leave/pending')}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '12px' }}>
                <div style={{ width: '40px', height: '40px', borderRadius: '10px', background: 'var(--brand-blue)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <ClipboardCheck size={20} color="white" />
                </div>
                <div style={{ fontWeight: 600, fontSize: 'var(--fs-h2)' }}>Review Pending Requests</div>
              </div>
              <div style={{ fontSize: 'var(--fs-body)', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                View and process leave applications submitted by makers. Approve or reject based on team capacity and policies.
              </div>
            </div>

            <div style={{ padding: '24px', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', transition: 'all 0.2s', cursor: 'pointer' }} onClick={() => navigate('/leave/calendar')}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '12px' }}>
                <div style={{ width: '40px', height: '40px', borderRadius: '10px', background: 'var(--success)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <Calendar size={20} color="white" />
                </div>
                <div style={{ fontWeight: 600, fontSize: 'var(--fs-h2)' }}>View Team Calendar</div>
              </div>
              <div style={{ fontSize: 'var(--fs-body)', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                See who's out on which dates. Plan team activities and ensure coverage during absences.
              </div>
            </div>

            <div style={{ padding: '24px', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', transition: 'all 0.2s', cursor: 'pointer' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '12px' }}>
                <div style={{ width: '40px', height: '40px', borderRadius: '10px', background: 'var(--draft)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <Users size={20} color="white" />
                </div>
                <div style={{ fontWeight: 600, fontSize: 'var(--fs-h2)' }}>View Employee Information</div>
              </div>
              <div style={{ fontSize: 'var(--fs-body)', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                Access employee details including leave balances, application history, and contact information.
              </div>
            </div>
          </div>
        </div>

        <div className="table-card" style={{ padding: '24px', marginTop: '24px' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px' }}>
            <h3 style={{ fontSize: 'var(--fs-h2)', fontWeight: 700, color: 'var(--text-primary)' }}>Recent Applications</h3>
            <button 
              onClick={() => navigate('/leave/pending')}
              style={{ background: 'none', border: 'none', color: 'var(--brand-blue)', fontSize: 'var(--fs-sm)', fontWeight: 600, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px' }}
            >
              View all <ArrowRight size={14} />
            </button>
          </div>
          
          {loading ? (
            <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-muted)' }}>Loading records...</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
              {recent.map(leave => <LeaveCard key={leave.id} leave={leave} />)}
              {recent.length === 0 && <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-muted)' }}>No leave applications yet. When your team applies, they appear here.</div>}
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="page" style={{ paddingBottom: '80px' }}>
      {showWelcome && (
        <div className="toast-notification">
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
          <span>Login successful! Welcome, {user?.name || 'User'}</span>
        </div>
      )}

      <div className="pg-head">
        <div className="pg-head-left">
          {/* <div className="pg-breadcrumb">Leave Management</div> */}
          <div className="pg-title">My Dashboard</div>
          {/* <div className="pg-desc">Manage your leave applications and track balances</div> */}
        </div>
        <div className="pg-head-right">
          <div className="pg-logo">
            <BrandLogo variant="letterhead" />
          </div>
        </div>
      </div>

      <div className="table-card" style={{ padding: '24px', marginBottom: '32px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px' }}>
          <h3 style={{ fontSize: 'var(--fs-h2)', fontWeight: 700, color: 'var(--text-primary)' }}>What You Can Do</h3>
        </div>
        
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '20px' }}>
          <div style={{ padding: '24px', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', transition: 'all 0.2s', cursor: 'pointer' }} onClick={() => navigate('/leave/apply')}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '12px' }}>
              <div style={{ width: '40px', height: '40px', borderRadius: '10px', background: 'var(--brand-blue)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Plus size={20} color="white" />
              </div>
              <div style={{ fontWeight: 600, fontSize: 'var(--fs-h2)' }}>Apply for Leave</div>
            </div>
            <div style={{ fontSize: 'var(--fs-body)', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
              Submit a new leave request for annual, sick, casual, or other leave types. Your application will be reviewed by your manager.
            </div>
          </div>

          <div style={{ padding: '24px', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', transition: 'all 0.2s', cursor: 'pointer' }} onClick={() => navigate('/leave/my-applications')}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '12px' }}>
              <div style={{ width: '40px', height: '40px', borderRadius: '10px', background: 'var(--draft)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <FileText size={20} color="white" />
              </div>
              <div style={{ fontWeight: 600, fontSize: 'var(--fs-h2)' }}>View My Applications</div>
            </div>
            <div style={{ fontSize: 'var(--fs-body)', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
              Track the status of your submitted leave applications. See pending, approved, or rejected requests.
            </div>
          </div>

          <div style={{ padding: '24px', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', transition: 'all 0.2s', cursor: 'pointer' }} onClick={() => navigate('/leave/calendar')}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '12px' }}>
              <div style={{ width: '40px', height: '40px', borderRadius: '10px', background: 'var(--success)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Calendar size={20} color="white" />
              </div>
              <div style={{ fontWeight: 600, fontSize: 'var(--fs-h2)' }}>View Team Calendar</div>
            </div>
            <div style={{ fontSize: 'var(--fs-body)', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
              See which team members are on leave on specific dates. Plan your time off accordingly.
            </div>
          </div>
        </div>
      </div>

      {error && (
        <div style={{ padding: '10px 14px', margin: '0 0 16px', borderRadius: 8, background: 'rgba(220,38,38,.08)', color: '#b91c1c', fontSize: 'var(--fs-sm)', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span>Some dashboard data could not be loaded. {error}</span>
          <button className="btn btn-ghost btn-sm" onClick={() => { fetchLeaves(); fetchBalances(); }}>Retry</button>
        </div>
      )}

      <AttendanceWidget />

      {/* Phase 6: enterprise leave-records summary (compact) */}
      <DashboardMemoCard />

      <DashboardRecordsSummary />

      <div className="ds-main-grid" style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 340px', gap: '32px' }}>

        {/* MAIN COLUMN */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '40px' }}>
          
          {/* Balances Section — category-aware (Phase 6) */}
          <CategoryBalances />

          {/* Recent Applications Section */}
          <section>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px' }}>
              <h3 style={{ fontSize: 'var(--fs-h2)', fontWeight: 700, color: 'var(--text-primary)' }}>Recent Applications</h3>
              <button 
                onClick={() => navigate('/leave/my-applications')}
                style={{ background: 'none', border: 'none', color: 'var(--brand-blue)', fontSize: 'var(--fs-sm)', fontWeight: 600, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px' }}
              >
                View full history <ArrowRight size={14} />
              </button>
            </div>
            
            <div className="table-card" style={{ padding: '24px' }}>
              {loading ? (
                <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-muted)' }}>Loading records...</div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                  {recent.map(leave => <LeaveCard key={leave.id} leave={leave} />)}
                  {recent.length === 0 && <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-muted)' }}>You haven’t applied for leave yet. <button onClick={() => navigate('/leave/apply')} style={{ background: 'none', border: 'none', color: 'var(--brand-blue)', fontWeight: 600, cursor: 'pointer', marginLeft: '8px' }}>Apply for leave</button></div>}
                </div>
              )}
            </div>
          </section>

        </div>

        {/* SIDEBAR COLUMN */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '32px' }}>
          
          {/* Leave Policy Widget — official category-based entitlements (live from
              the entitlement engine; see LeavePolicyWidget). */}
          <LeavePolicyWidget />

          {/* Tips Widget */}
          <div className="side-card" style={{ padding: '28px' }}>
            <h3 style={{ fontSize: 'var(--fs-sm)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '1.5px', marginBottom: '24px', color: 'var(--text-muted)' }}>Tips</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <div style={{ fontSize: 'var(--fs-body)', color: 'var(--text-secondary)', lineHeight: 1.5, padding: '12px', background: 'var(--bg-main)', borderRadius: 'var(--radius-sm)' }}>
                Plan ahead! Submit leave requests at least 3 days in advance for planned leaves.
              </div>
              <div style={{ fontSize: 'var(--fs-body)', color: 'var(--text-secondary)', lineHeight: 1.5, padding: '12px', background: 'var(--bg-main)', borderRadius: 'var(--radius-sm)' }}>
                For urgent leaves, mark priority as "Urgent" and provide clear reason.
              </div>
              <div style={{ fontSize: 'var(--fs-body)', color: 'var(--text-secondary)', lineHeight: 1.5, padding: '12px', background: 'var(--bg-main)', borderRadius: 'var(--radius-sm)' }}>
                Always add handover notes to ensure smooth workflow during your absence.
              </div>
            </div>
          </div>

        </div>
      </div>
    </div>
  );
};

export default LeaveDashboard;
