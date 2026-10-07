import React, { useState, useEffect } from 'react';
import BrandLogo from '../../components/branding/BrandLogo';
import { useNavigate } from 'react-router-dom';
import { useLeaves } from '../../hooks/useLeaves';
import { useAuth } from '../../hooks/useAuth';
import { adminService } from '../../services/adminService';
import { leaveService, leaveTypeLabel } from '../../services/leaveService';
import { roleLabel, can } from '../../services/roles';
import { Calendar, Clock, User, FileText, ArrowLeft, Send } from 'lucide-react';
import EmptyState from '../../components/common/EmptyState';
import { EMPTY } from '../../services/emptyStates';

const ApplyLeave = () => {
  const navigate = useNavigate();
  const { applyLeave, loading } = useLeaves();
  const { role, user } = useAuth();
  const [managers, setManagers] = useState([]);
  // Leave types this user's category may apply for (from the entitlement engine).
  const [leaveTypes, setLeaveTypes] = useState([]);

  useEffect(() => {
    const fetchManagers = async () => {
      try {
        const users = await adminService.getUsers();
        // Reporting managers = Department Heads (checker) and HR (approver) - for
        // an employee. A Department Head's or Board member's leave is decided by
        // the Board of Directors (Phase BOD-ROLE-EXECUTIVE-GOVERNANCE), so for
        // them the list is the OTHER Board members: offering a fellow head here
        // would let the form send the request somewhere the server will not honour.
        const byBoard = ['checker', 'bod'].includes(role);
        const active = users.filter((u) => u.is_active !== false);
        let mgrs = active.filter((u) => (byBoard
          ? u.role === 'bod' && String(u.id) !== String(user?.id)
          : ['approver', 'checker'].includes(u.role)));
        // No other active Board member: the server hands the request to HR
        // (leaves.approvals), so the picker must offer HR too. An empty required
        // list would leave a Department Head unable to apply at all.
        if (byBoard && mgrs.length === 0) {
          mgrs = active.filter((u) => u.role === 'approver');
        }
        setManagers(mgrs);
      } catch (err) {
        console.error('Error fetching users:', err);
      }
    };
    const fetchTypes = async () => {
      try {
        const ent = await leaveService.getEntitlements();
        // applicable_types are uppercase codes (ANNUAL, SICK, COMPENSATORY, …).
        const labels = (ent.applicable_types || []).map((c) => leaveTypeLabel(c.toLowerCase()));
        setLeaveTypes(labels);
      } catch {
        setLeaveTypes(['Annual Leave', 'Sick Leave']); // safe fallback
      }
    };
    fetchManagers();
    fetchTypes();
  // Re-read once the role is known: the list depends on it (Phase BOD).
  }, [role, user?.id]);

  const [formData, setFormData] = useState({
    type: 'Annual Leave',
    priority: 'Normal',
    day_portion: 'full',
    start: '',
    end: '',
    manager: '',
    approver_id: '',
    contact: '',
    reason: '',
    handover: ''
  });
  const [error, setError] = useState(null);
  const [successMessage, setSuccessMessage] = useState('');

  // Keep the selected type valid for the user's category (default to the first).
  // Derived rather than written back into formData by an effect: the effect
  // needed an exhaustive-deps suppression, and that suppression also switched
  // off the hooks linter's analysis of this whole component. Both the select
  // and the submitted payload read this one value, so they cannot disagree.
  const leaveType = leaveTypes.length && !leaveTypes.includes(formData.type)
    ? leaveTypes[0]
    : formData.type;

  // Employees, Department Heads and HR may apply for their own leave; Admin is an
  // oversight role and cannot (backend also 403s admins). Central gate = roles.js.
  if (!can(role, 'applyLeave')) {
    return (
      <div className="page">
        <div className="pg-head">
          <div>
            <div className="pg-title">Access Denied</div>
            <div className="pg-desc">Admins manage and approve leave and do not submit personal applications.</div>
          </div>
        </div>
        <EmptyState variant="denied" title={EMPTY.noPermission} />
      </div>
    );
  }

  const handleChange = (e) => {
    setFormData({ ...formData, [e.target.id.replace('lv-', '')]: e.target.value });
  };

  const showSuccess = (message) => {
    setSuccessMessage(message);
    window.setTimeout(() => setSuccessMessage(''), 3500);
  };

  const extractError = (err) => {
    const data = err?.response?.data;
    if (!data) return err?.message || 'Failed to submit application';
    if (Array.isArray(data)) return data.join(' ');
    if (typeof data === 'string') return data;
    if (data.detail) return data.detail;
    if (typeof data === 'object') {
      // DRF field errors: {"field": ["msg", ...], ...}
      const parts = Object.entries(data).map(([k, v]) => {
        const val = Array.isArray(v) ? v.join(' ') : v;
        return k === 'non_field_errors' ? val : `${k}: ${val}`;
      });
      if (parts.length) return parts.join(' \n');
    }
    return err?.message || 'Failed to submit application';
  };

  const handleSubmit = async () => {
    setError(null);

    if (!formData.start || !formData.end || !formData.approver_id || !formData.reason) {
      setError('Please fill all required fields, including Reporting Manager.');
      return;
    }

    if (new Date(formData.end) < new Date(formData.start)) {
      setError('End date must be after start date');
      return;
    }

    try {
      await applyLeave({ ...formData, type: leaveType });
      showSuccess('Leave application submitted successfully!');
      setTimeout(() => navigate('/leave/my-applications'), 1500);
    } catch (err) {
      setError(extractError(err));
    }
  };

  return (
    <div className="page">
      {(error || successMessage) && (
        <div className="popup-overlay" onClick={() => { setError(null); setSuccessMessage(''); }}>
          <div className="popup-modal" onClick={e => e.stopPropagation()}>
            {error && (
              <>
                <div className="popup-icon popup-error-icon">
                  <svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
                </div>
                <h3>Submission Failed</h3>
                <p>{error}</p>
              </>
            )}
            {successMessage && (
              <>
                <div className="popup-icon popup-success-icon">
                  <svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
                </div>
                <h3>Application Submitted</h3>
                <p>{successMessage}</p>
              </>
            )}
            <button className="popup-btn" onClick={() => { setError(null); setSuccessMessage(''); }}>OK</button>
          </div>
        </div>
      )}

      <div className="pg-head">
        <div className="pg-head-left">
          <div className="pg-breadcrumb">
            <button className="pg-back" aria-label="Back" onClick={() => navigate(-1)}>
              <ArrowLeft size={18} />
            </button>
            Leave Management
          </div>
          <div className="pg-title">Apply for Leave</div>
          <div className="pg-desc">Submit a new leave application for approval</div>
        </div>
        <div className="pg-head-right">
          <div className="pg-logo">
            <BrandLogo variant="letterhead" />
          </div>
        </div>
      </div>

      <div className="table-card" style={{ padding: '24px' }}>
        <div className="fgrid">
          <div className="fg">
            <label htmlFor="lv-type"><Calendar size={16} /> Leave Type <span className="req">*</span></label>
            <select id="lv-type" value={leaveType} onChange={handleChange}>
              {leaveTypes.length === 0 && <option value="">No leave types available for your category</option>}
              {leaveTypes.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div className="fg">
            <label htmlFor="lv-priority"><Clock size={16} /> Priority</label>
            <select id="lv-priority" value={formData.priority} onChange={handleChange}>
              <option>Normal</option>
              <option>Urgent</option>
            </select>
          </div>
        </div>
        <div className="fgrid">
          <div className="fg">
            <label htmlFor="lv-day_portion"><Clock size={16} /> Duration <span className="req">*</span></label>
            <select
              id="lv-day_portion"
              value={formData.day_portion}
              onChange={(e) => {
                const dp = e.target.value;
                // A half day is a single date — collapse the range onto the start.
                setFormData((f) => ({ ...f, day_portion: dp, end: dp === 'full' ? f.end : f.start }));
              }}
            >
              <option value="full">Full Day</option>
              <option value="first_half">Half Day — First Half (morning)</option>
              <option value="second_half">Half Day — Second Half (afternoon)</option>
            </select>
          </div>
          <div className="fg" />
        </div>
        <div className="fgrid">
          <div className="fg">
            <label htmlFor="lv-start"><Calendar size={16} /> Start Date <span className="req">*</span></label>
            <input
              type="date"
              id="lv-start"
              value={formData.start}
              onChange={(e) => {
                const start = e.target.value;
                // Keep end pinned to start while a half day is selected.
                setFormData((f) => ({ ...f, start, end: f.day_portion === 'full' ? f.end : start }));
              }}
            />
          </div>
          <div className="fg">
            <label htmlFor="lv-end"><Calendar size={16} /> End Date <span className="req">*</span></label>
            <input
              type="date"
              id="lv-end"
              value={formData.end}
              disabled={formData.day_portion !== 'full'}
              onChange={handleChange}
            />
            {formData.day_portion !== 'full' && (
              <span className="fg-hint" style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-muted)' }}>
                A half day applies to a single date.
              </span>
            )}
          </div>
        </div>
        <div className="fgrid">
          <div className="fg">
            <label htmlFor="lv-manager"><User size={16} /> Reporting Manager <span className="req">*</span></label>
              <select id="lv-manager" value={formData.approver_id} onChange={(e) => {
                const selectedId = e.target.value;
                const selected = managers.find(m => m.id === selectedId);
                const name = selected ? (`${selected.first_name || ''} ${selected.last_name || ''}`.trim() || selected.username || selected.email) : '';
                setFormData({ ...formData, manager: name, approver_id: selectedId });
              }}>
                <option value="">Select Manager</option>
                {managers.map(m => {
                  const name = m.first_name && m.last_name ? `${m.first_name} ${m.last_name}` : m.username || m.email;
                  return (
                    <option key={m.id} value={m.id}>
                      {name} — {roleLabel(m.role)}
                    </option>
                  );
                })}
              </select>
          </div>
          <div className="fg">
            <label htmlFor="lv-contact"><User size={16} /> Contact During Leave</label>
            <input type="text" id="lv-contact" placeholder="Phone or email" value={formData.contact} onChange={handleChange} />
          </div>
        </div>
        <div className="fg">
          <label htmlFor="lv-reason"><FileText size={16} /> Reason for Leave <span className="req">*</span></label>
          <textarea id="lv-reason" placeholder="Provide detailed reason for your leave application..." value={formData.reason} onChange={handleChange}></textarea>
        </div>
        <div className="fg">
          <label htmlFor="lv-handover"><FileText size={16} /> Work Handover Notes</label>
          <textarea id="lv-handover" placeholder="Describe any pending tasks or handover instructions..." style={{ minHeight: '60px' }} value={formData.handover} onChange={handleChange}></textarea>
        </div>
        <div style={{ display: 'flex', gap: '10px', marginTop: '16px' }}>
          <button className="btn btn-ghost" onClick={() => navigate(-1)} disabled={loading}>
            <ArrowLeft size={16} /> Cancel
          </button>
          <button className="btn btn-primary" onClick={handleSubmit} disabled={loading}>
            {loading ? 'Submitting...' : <>Submit Application <Send size={16} /></>}
          </button>
        </div>
      </div>
    </div>
  );
};

export default ApplyLeave;
