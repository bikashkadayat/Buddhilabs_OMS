import React, { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, X } from 'lucide-react';
import { adminLeaveService } from '../../../services/adminLeaveService';
import { days } from '../../../utils/leaveFormat';
import AttendanceIndicator from '../../../components/leave-records/AttendanceIndicator';
import Toast from '../../../components/admin/Toast';
import { Skeleton, ErrorState } from '../../../components/leave-records/States';

const TABS = ['Overview', 'Balances', 'Applications', 'Adjustments', 'Audit'];

/** Modal form to grant/deduct days on one balance (delta + mandatory reason). */
const AdjustModal = ({ balance, busy, onClose, onSubmit }) => {
  const [delta, setDelta] = useState('1');
  const [reason, setReason] = useState('');
  const valid = delta.trim() !== '' && !Number.isNaN(Number(delta)) && reason.trim().length >= 5;
  return (
    <div className="lr-modal-overlay" role="dialog" aria-modal="true" aria-label="Adjust balance" onClick={onClose}>
      <div className="lr-modal" onClick={(e) => e.stopPropagation()}>
        <div className="lr-modal-head">
          <h3>Adjust {balance.leave_type_name || balance.leave_type_code}</h3>
          <button type="button" className="lr-modal-close" aria-label="Close" onClick={onClose}><X size={18} aria-hidden="true" /></button>
        </div>
        <p style={{ fontSize: 'var(--fs-body)', color: 'var(--text-secondary)', margin: '0 0 12px' }}>
          Positive grants days, negative deducts. Recorded in the audit log with your reason.
        </p>
        <label className="lr-field">
          <span>Delta (days)</span>
          <input type="number" step="0.5" value={delta} onChange={(e) => setDelta(e.target.value)} aria-label="Adjustment in days" />
        </label>
        <label className="lr-field">
          <span>Reason <span aria-hidden="true">*</span></span>
          <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Required (min 5 characters)" aria-label="Reason" />
        </label>
        <div className="lr-modal-actions">
          <button type="button" className="lr-btn lr-btn-ghost" onClick={onClose}>Cancel</button>
          <button type="button" className="lr-btn lr-btn-primary" disabled={!valid || busy} onClick={() => onSubmit(delta, reason)}>
            {busy ? 'Working…' : 'Apply adjustment'}
          </button>
        </div>
      </div>
    </div>
  );
};

const EmployeeDetail = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const year = new Date().getFullYear();
  const [tab, setTab] = useState('Overview');
  const [adjustFor, setAdjustFor] = useState(null);
  const [toast, setToast] = useState(null);

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['admin-employee', id, year],
    queryFn: () => adminLeaveService.getEmployee(id, year),
  });

  const adjust = useMutation({
    mutationFn: ({ leaveTypeCode, delta, reason }) =>
      adminLeaveService.adjustBalance(id, { leave_type: leaveTypeCode, year, delta, reason }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-employee', id, year] });
      setToast({ message: 'Balance adjusted.', tone: 'success' });
      setAdjustFor(null);
    },
    onError: () => setToast({ message: 'Adjustment failed.', tone: 'error' }),
  });

  if (isLoading) return <div className="page"><Skeleton rows={4} /></div>;
  if (isError) return <div className="page"><ErrorState error={error} onRetry={refetch} /></div>;

  const emp = data.employee;
  const balances = data.balances ?? [];
  const applications = data.applications ?? [];
  // Adjustments derived from balances that carry a non-zero manual adjustment.
  const adjustments = balances.filter((b) => Number(b.adjustment_days) !== 0);

  return (
    <div className="page" style={{ paddingBottom: 80 }}>
      <button type="button" className="lr-btn lr-btn-ghost" onClick={() => navigate('/admin/leaves/employees')} style={{ marginBottom: 16 }}>
        <ArrowLeft size={14} /> Back to employees
      </button>

      <div className="lr-page-head">
        <div>
          <h2>{emp.full_name}</h2>
          <div className="lr-page-sub">{emp.email} · {emp.role} · {emp.department || 'No department'}</div>
        </div>
      </div>

      <div className="lr-tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t} type="button" role="tab" aria-selected={tab === t}
            className={`lr-tab ${tab === t ? 'on' : ''}`} onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </div>

      <div style={{ marginTop: 20 }}>
        {tab === 'Overview' && (
          <div className="lr-balance-grid">
            <div className="lr-balance-card lr-balance-compact"><div className="lr-bc-type">Used ({year})</div><div className="lr-bc-available"><span className="lr-bc-available-num">{days(emp.used_days)}</span></div></div>
            <div className="lr-balance-card lr-balance-compact"><div className="lr-bc-type">Available</div><div className="lr-bc-available"><span className="lr-bc-available-num">{days(emp.available_days)}</span></div></div>
            <div className="lr-balance-card lr-balance-compact"><div className="lr-bc-type">Attendance</div><div style={{ marginTop: 12 }}><AttendanceIndicator percentage={emp.attendance_percentage} /></div></div>
          </div>
        )}

        {tab === 'Balances' && (
          <div className="lr-table-wrap">
            <table className="lr-table">
              <thead><tr><th scope="col">Type</th><th scope="col">Entitled</th><th scope="col">Used</th><th scope="col">Pending</th><th scope="col">Adjustment</th><th scope="col">Available</th><th scope="col">Actions</th></tr></thead>
              <tbody>
                {balances.map((b) => (
                  <tr key={b.leave_type_code}>
                    <td>{b.leave_type_name || b.leave_type_code}</td>
                    <td>{days(b.entitled_days)}</td><td>{days(b.used_days)}</td>
                    <td>{days(b.pending_days)}</td><td>{days(b.adjustment_days)}</td>
                    <td><b>{days(b.available_days)}</b></td>
                    <td><button type="button" className="lr-btn lr-btn-ghost" onClick={() => setAdjustFor(b)}>Adjust</button></td>
                  </tr>
                ))}
                {balances.length === 0 && <tr><td colSpan={7} style={{ color: 'var(--text-muted)' }}>No balances for {year}.</td></tr>}
              </tbody>
            </table>
          </div>
        )}

        {tab === 'Applications' && (
          <div className="lr-table-wrap">
            <table className="lr-table">
              <thead><tr><th scope="col">Type</th><th scope="col">From</th><th scope="col">To</th><th scope="col">Status</th></tr></thead>
              <tbody>
                {applications.map((l) => (
                  <tr key={l.id}><td>{l.leave_type}</td><td>{l.start_date}</td><td>{l.end_date}</td><td style={{ textTransform: 'capitalize' }}>{l.status}</td></tr>
                ))}
                {applications.length === 0 && <tr><td colSpan={4} style={{ color: 'var(--text-muted)' }}>No applications.</td></tr>}
              </tbody>
            </table>
          </div>
        )}

        {tab === 'Adjustments' && (
          <div className="lr-table-wrap">
            <table className="lr-table">
              <thead><tr><th scope="col">Type</th><th scope="col">Adjustment (days)</th><th scope="col">Available</th></tr></thead>
              <tbody>
                {adjustments.map((b) => (
                  <tr key={b.leave_type_code}><td>{b.leave_type_name || b.leave_type_code}</td><td>{days(b.adjustment_days)}</td><td>{days(b.available_days)}</td></tr>
                ))}
                {adjustments.length === 0 && <tr><td colSpan={3} style={{ color: 'var(--text-muted)' }}>No manual adjustments recorded. Full history is in the AuditLog.</td></tr>}
              </tbody>
            </table>
          </div>
        )}

        {tab === 'Audit' && (
          <p className="lr-page-sub">This employee's full action history is available in the Audit Log
            (<code>/api/v1/audit/</code>, admin only). A dedicated per-employee audit view arrives with Reports in Phase 8.</p>
        )}
      </div>

      {adjustFor && (
        <AdjustModal
          balance={adjustFor}
          busy={adjust.isPending}
          onClose={() => setAdjustFor(null)}
          onSubmit={(delta, reason) => adjust.mutate({ leaveTypeCode: adjustFor.leave_type_code, delta, reason })}
        />
      )}

      {toast && <Toast {...toast} onClose={() => setToast(null)} />}
    </div>
  );
};

export default EmployeeDetail;
