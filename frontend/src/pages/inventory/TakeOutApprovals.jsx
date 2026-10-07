import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, CheckCircle, XCircle } from 'lucide-react';
import Badge from '../../components/common/Badge';
import { useAuth } from '../../hooks/useAuth';
import { useAutoRefresh } from '../../hooks/useAutoRefresh';
import { useBodyScrollLock } from '../../hooks/useBodyScrollLock';
import { inventoryService } from '../../services/inventoryService';
import EmptyState from '../../components/common/EmptyState';
import { EMPTY } from '../../services/emptyStates';

const MANAGER_ROLES = ['admin', 'approver', 'checker'];

const TakeOutApprovals = () => {
  const navigate = useNavigate();
  const { role } = useAuth();
  const isManager = MANAGER_ROLES.includes(role);

  const [requests, setRequests] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [popup, setPopup] = useState(null);
  const [rejecting, setRejecting] = useState(null);   // request being rejected
  const [remarks, setRemarks] = useState('');
  const [remarkErr, setRemarkErr] = useState('');
  const [busy, setBusy] = useState(null);
  useBodyScrollLock(!!(rejecting || popup));

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try { setRequests(await inventoryService.takeouts({ status: 'pending' })); }
    catch (e) { setError(e?.response?.data?.detail || e.message || 'Failed to load pending requests.'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);
  useAutoRefresh(load, 20000);

  const approve = async (r) => {
    setBusy(r.id);
    try { await inventoryService.approveTakeout(r.id); await load(); setPopup({ type: 'success', msg: 'Request approved. Item marked out.' }); }
    catch (e) { setPopup({ type: 'error', msg: e?.response?.data?.detail || 'Could not approve.' }); }
    finally { setBusy(null); }
  };

  const doReject = async () => {
    if (!remarks.trim()) { setRemarkErr('A reason is required to reject.'); return; }
    setBusy(rejecting.id);
    try {
      await inventoryService.rejectTakeout(rejecting.id, remarks.trim());
      setRejecting(null); setRemarks(''); await load();
      setPopup({ type: 'success', msg: 'Request rejected. Requester notified.' });
    } catch (e) {
      setPopup({ type: 'error', msg: e?.response?.data?.detail || 'Could not reject.' });
    } finally { setBusy(null); }
  };

  if (!isManager) {
    return (
      <div className="page"><EmptyState variant="denied" title={EMPTY.noPermission} /></div>
    );
  }

  return (
    <div className="page">
      {popup && (
        <div className="popup-overlay" onClick={() => setPopup(null)}>
          <div className="popup-modal" onClick={e => e.stopPropagation()}>
            <h3>{popup.type === 'success' ? 'Done' : 'Error'}</h3>
            <p>{popup.msg}</p>
            <button className="popup-btn" onClick={() => setPopup(null)}>OK</button>
          </div>
        </div>
      )}

      <div className="pg-head">
        <div className="pg-head-left">
          <div className="pg-breadcrumb">
            <button className="pg-back" aria-label="Back" onClick={() => navigate('/inventory')}><ArrowLeft size={18} /></button>
            Inventory
          </div>
          <div className="pg-title">Take-Out Approvals</div>
          <div className="pg-desc">Review and decide item take-out requests</div>
        </div>
      </div>

      {loading ? (
        <div style={{ padding: 24 }}>Loading pending requests…</div>
      ) : error ? (
        <EmptyState variant="error" title="Could not load pending requests."
          body={error} onAction={load} actionLabel="Retry" />
      ) : requests.length === 0 ? (
        <EmptyState variant="cleared" title="No take-out requests are waiting." />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {requests.map(r => (
            <div key={r.id} style={{ background: 'var(--white)', border: '1px solid var(--border)', borderRadius: 'var(--r2)', padding: 20, boxShadow: 'var(--shadow)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, borderBottom: '1px solid var(--border-light)', paddingBottom: 12, marginBottom: 12, flexWrap: 'wrap' }}>
                <div>
                  <div style={{ fontWeight: 600 }}>{r.item_name} <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>({r.item_code})</span></div>
                  <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-muted)' }}>{r.reference} · by {r.requested_by_name}</div>
                </div>
                <Badge status={r.status} />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12, fontSize: 'var(--fs-sm)', marginBottom: 14 }}>
                <div><div style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>Purpose</div>{r.purpose_display}</div>
                <div><div style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>Out → Return</div>{r.expected_out_date} → {r.expected_return_date}<div style={{ color: 'var(--text-muted)', fontSize: 'var(--fs-meta)' }}>{r.expected_out_date_bs} → {r.expected_return_date_bs} BS</div></div>
                <div><div style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>Department</div>{r.department_name || '—'}</div>
                <div style={{ gridColumn: '1 / -1' }}><div style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>Reason</div>{r.reason || '—'}</div>
              </div>
              <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', borderTop: '1px solid var(--border-light)', paddingTop: 14 }}>
                <button className="btn btn-ghost" disabled={busy === r.id} onClick={() => { setRejecting(r); setRemarks(''); setRemarkErr(''); }}>
                  <XCircle size={16} /> Reject
                </button>
                <button className="btn btn-success" disabled={busy === r.id} onClick={() => approve(r)}>
                  <CheckCircle size={16} /> {busy === r.id ? 'Working…' : 'Approve'}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {rejecting && (
        <div className="modal-overlay" onClick={() => busy || setRejecting(null)}>
          <div className="modal-content" style={{ maxWidth: 460 }} onClick={e => e.stopPropagation()}>
            <h3 style={{ margin: '0 0 4px', fontSize: 'var(--fs-h2)' }}>Reject Request</h3>
            <p style={{ color: 'var(--text-muted)', fontSize: 'var(--fs-sm)', margin: '0 0 16px' }}>{rejecting.reference} · {rejecting.item_name}</p>
            <div className="fg">
              <label>Reason <span className="req">*</span></label>
              <textarea rows={3} style={{ minHeight: 90 }} value={remarks}
                onChange={e => { setRemarks(e.target.value); if (remarkErr) setRemarkErr(''); }} />
            </div>
            {remarkErr && <div style={{ color: 'var(--brand-red)', fontSize: 'var(--fs-sm)', marginBottom: 12 }}>{remarkErr}</div>}
            <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end' }}>
              <button className="btn btn-ghost" disabled={busy} onClick={() => setRejecting(null)}>Cancel</button>
              <button className="btn btn-primary" disabled={busy} onClick={doReject}>{busy ? 'Rejecting…' : 'Confirm Reject'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default TakeOutApprovals;
