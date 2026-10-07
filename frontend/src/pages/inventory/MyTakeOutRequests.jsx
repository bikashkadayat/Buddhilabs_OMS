import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, Plus, FileText } from 'lucide-react';
import Badge from '../../components/common/Badge';
import { useAutoRefresh } from '../../hooks/useAutoRefresh';
import { useBodyScrollLock } from '../../hooks/useBodyScrollLock';
import { inventoryService } from '../../services/inventoryService';
import EligibleAssetSelect from '../../components/inventory/EligibleAssetSelect';
import Skeleton from '../../components/common/Skeleton';

const emptyForm = { item: '', purpose: 'home', reason: '', expected_out_date: '', expected_return_date: '' };

const MyTakeOutRequests = () => {
  const navigate = useNavigate();
  const [requests, setRequests] = useState([]);
  const [availableItems, setAvailableItems] = useState([]);
  // Phase 70.19-F: the selector's own state, so "loading", "failed" and "nothing
  // eligible" stay distinguishable instead of all collapsing into an empty list.
  const [itemsLoading, setItemsLoading] = useState(false);
  const [itemsError, setItemsError] = useState(null);
  const [itemsEmptyReason, setItemsEmptyReason] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const [submitting, setSubmitting] = useState(false);
  const [popup, setPopup] = useState(null);
  useBodyScrollLock(!!(showForm || popup));

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const mine = await inventoryService.takeouts({ mine: 1 });
      setRequests(mine);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Failed to load your requests.');
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);
  useAutoRefresh(load, 30000);

  // Phase 70.19-E. Was two calls to inventoryService.items({status}) — the asset
  // register, which InventoryItemPermission denies to every non-manager. Both
  // returned 403, a bare `catch` turned that into [], and the dropdown was empty
  // for exactly the people the form exists for. One call to the dedicated
  // eligible-assets endpoint now answers the question actually being asked.
  const loadEligibleAssets = useCallback(async () => {
    setItemsLoading(true); setItemsError(null); setItemsEmptyReason(null);
    try {
      const data = await inventoryService.takeoutEligibleAssets();
      setAvailableItems(data?.results || []);
      setItemsEmptyReason(data?.empty_reason || null);
    } catch (e) {
      // Never swallow: a failure the user cannot see is a failure they report as
      // "the dropdown is empty", which is what cost Phase 70.18 an investigation.
      setAvailableItems([]);
      setItemsError(e?.response?.data?.detail || e.message || 'Could not reach the server.');
    } finally { setItemsLoading(false); }
  }, []);

  const openForm = async () => {
    setForm(emptyForm); setShowForm(true);
    await loadEligibleAssets();
  };

  const submit = async () => {
    if (!form.item || !form.reason.trim() || !form.expected_out_date || !form.expected_return_date) return;
    setSubmitting(true);
    try {
      await inventoryService.createTakeout(form);
      setShowForm(false); await load();
      setPopup({ type: 'success', msg: 'Take-out request submitted for approval.' });
    } catch (e) {
      setPopup({ type: 'error', msg: e?.response?.data?.detail || e?.response?.data?.reason || 'Could not submit the request.' });
    } finally { setSubmitting(false); }
  };

  return (
    <div className="page">
      {popup && (
        <div className="popup-overlay" onClick={() => setPopup(null)}>
          <div className="popup-modal" onClick={e => e.stopPropagation()}>
            <h3>{popup.type === 'success' ? 'Submitted' : 'Error'}</h3>
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
          <div className="pg-title">My Take-Out Requests</div>
          <div className="pg-desc">Request approval to take an office item home or outside</div>
        </div>
        <div className="pg-head-right">
          <button className="btn btn-primary" onClick={openForm}><Plus size={16} /> New Request</button>
        </div>
      </div>

      <div className="table-card">
        <div className="tc-top"><span className="tc-title">Requests</span></div>
        {loading ? (
          <Skeleton rows={3} label="Loading requests" />
        ) : error ? (
          <div style={{ padding: 32, textAlign: 'center' }}>
            <div style={{ color: 'var(--danger, #b91c1c)', marginBottom: 12 }}>{error}</div>
            <button className="btn btn-primary" onClick={load}>Retry</button>
          </div>
        ) : (
          <div className="resp-table-wrap">
            <table className="resp-table">
              <thead>
                <tr><th>Reference</th><th>Item</th><th>Purpose</th><th>Dates</th><th>Status</th><th>Gate Pass</th></tr>
              </thead>
              <tbody>
                {requests.map(r => (
                  <tr key={r.id}>
                    <td data-label="Reference"><strong>{r.reference}</strong><div className="leave-meta" style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-muted)' }}>{r.created_at_bs} BS</div></td>
                    <td data-label="Item">{r.item_name}<div className="leave-meta" style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-muted)' }}>{r.item_code}</div></td>
                    <td data-label="Purpose">{r.purpose_display}</td>
                    <td data-label="Dates"><div style={{ fontSize: 'var(--fs-sm)' }}>{r.expected_out_date} → {r.expected_return_date}</div><div className="leave-meta" style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-muted)' }}>{r.expected_out_date_bs} → {r.expected_return_date_bs} BS</div></td>
                    <td data-label="Status"><Badge status={r.status} />{r.status === 'rejected' && r.approver_remarks && <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-muted)', marginTop: 4 }}>{r.approver_remarks}</div>}</td>
                    <td data-label="Gate Pass">
                      {['approved', 'returned'].includes(r.status)
                        ? <button className="btn btn-ghost btn-sm" onClick={() => inventoryService.gatePass(r.id)}><FileText size={14} /> PDF</button>
                        : <span style={{ color: 'var(--text-muted)', fontSize: 'var(--fs-meta)' }}>—</span>}
                    </td>
                  </tr>
                ))}
                {requests.length === 0 && (
                  <tr><td colSpan="6" style={{ textAlign: 'center', padding: 60, color: 'var(--text-muted)' }}>No take-out requests yet.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showForm && (
        <div className="modal-overlay" onClick={() => !submitting && setShowForm(false)}>
          <div className="modal-content" style={{ maxWidth: 560 }} onClick={e => e.stopPropagation()}>
            <h3 style={{ margin: '0 0 20px', fontSize: 'var(--fs-h2)' }}>New Take-Out Request</h3>
            <div className="fg">
              <label>Item <span className="req">*</span></label>
              <EligibleAssetSelect
                assets={availableItems}
                value={form.item}
                onChange={id => setForm({ ...form, item: id })}
                loading={itemsLoading}
                error={itemsError}
                emptyReason={itemsEmptyReason}
                onRetry={loadEligibleAssets}
                disabled={submitting}
              />
            </div>
            <div className="fg">
              <label>Purpose <span className="req">*</span></label>
              <select value={form.purpose} onChange={e => setForm({ ...form, purpose: e.target.value })}>
                <option value="home">Take Home</option>
                <option value="outside">Use Outside Office</option>
                <option value="repair">Repair / Servicing</option>
              </select>
            </div>
            <div className="fgrid">
              <div className="fg">
                <label>Take-Out Date <span className="req">*</span></label>
                <input type="date" value={form.expected_out_date} onChange={e => setForm({ ...form, expected_out_date: e.target.value })} />
              </div>
              <div className="fg">
                <label>Return By <span className="req">*</span></label>
                <input type="date" value={form.expected_return_date} onChange={e => setForm({ ...form, expected_return_date: e.target.value })} />
              </div>
            </div>
            <div className="fg">
              <label>Reason <span className="req">*</span></label>
              <textarea rows={3} style={{ minHeight: 90 }} value={form.reason} placeholder="Why do you need to take this item out?"
                onChange={e => setForm({ ...form, reason: e.target.value })} />
            </div>
            <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end' }}>
              <button className="btn btn-ghost" disabled={submitting} onClick={() => setShowForm(false)}>Cancel</button>
              <button className="btn btn-primary" disabled={submitting || !form.item || !form.reason.trim() || !form.expected_out_date || !form.expected_return_date} onClick={submit}>
                {submitting ? 'Submitting…' : 'Submit'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default MyTakeOutRequests;
