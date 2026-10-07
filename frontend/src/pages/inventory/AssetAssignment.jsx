import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, RotateCcw, Repeat, UserPlus, Users, Package } from 'lucide-react';
import { useAuth } from '../../hooks/useAuth';
import { useAutoRefresh } from '../../hooks/useAutoRefresh';
import { useBodyScrollLock } from '../../hooks/useBodyScrollLock';
import { inventoryService } from '../../services/inventoryService';
import Skeleton from '../../components/common/Skeleton';
import EmptyState from '../../components/common/EmptyState';
import { EMPTY } from '../../services/emptyStates';

const MANAGER_ROLES = ['admin', 'approver', 'checker'];
const CONDITIONS = [['new', 'New'], ['good', 'Good'], ['fair', 'Fair'], ['damaged', 'Damaged']];
const today = () => new Date().toISOString().slice(0, 10);

const Search = ({ placeholder, value, onChange }) => (
  <input placeholder={placeholder} value={value} onChange={e => onChange(e.target.value)} style={{ marginBottom: 8 }} />
);

const initials = (name) => (name || '?').trim().split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase() || '?';
const COND_STYLE = {
  new: { background: '#ecfdf5', color: '#065f46' },
  good: { background: '#eff6ff', color: '#1e40af' },
  fair: { background: '#fffbeb', color: '#b45309' },
  damaged: { background: '#fef2f2', color: '#b91c1c' },
};
const Cond = ({ value, label }) => (value
  ? <span className="wf-cond" style={COND_STYLE[value] || { background: 'var(--bg-main)', color: 'var(--text-secondary)' }}>{label || value}</span>
  : <span style={{ color: 'var(--text-muted)' }}>—</span>);
const Who = ({ name }) => (
  <div className="wf-who"><span className="wf-avatar">{initials(name)}</span><span className="wf-who-name">{name || '—'}</span></div>
);

const AssetAssignment = () => {
  const navigate = useNavigate();
  const { role } = useAuth();
  const isManager = MANAGER_ROLES.includes(role);

  const [rows, setRows] = useState([]);
  const [categories, setCategories] = useState([]);
  const [employees, setEmployees] = useState([]);
  const [assignables, setAssignables] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [sourcesErr, setSourcesErr] = useState('');
  const [sourcesLoading, setSourcesLoading] = useState(true);
  const [popup, setPopup] = useState(null);
  const [groupByEmp, setGroupByEmp] = useState(false);
  const [saving, setSaving] = useState(false);

  const [filters, setFilters] = useState({ employee: '', department: '', category: '', status: '', search: '' });
  const [assign, setAssign] = useState({ item: '', assigned_to: '', assigned_date: today(), handover_condition: 'good', accessories: '', note: '' });
  const [itemQ, setItemQ] = useState('');
  const [empQ, setEmpQ] = useState('');

  const [returnCtx, setReturnCtx] = useState(null);
  const [returnForm, setReturnForm] = useState({ return_condition: 'good', return_remarks: '' });
  const [handoverCtx, setHandoverCtx] = useState(null);
  const [handoverForm, setHandoverForm] = useState({ assigned_to: '', assigned_date: today(), handover_condition: 'good', note: '' });
  const [hoEmpQ, setHoEmpQ] = useState('');
  useBodyScrollLock(!!(returnCtx || handoverCtx || popup));

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const params = Object.fromEntries(Object.entries(filters).filter(([, v]) => v));
      setRows(await inventoryService.board(params));
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Failed to load assignments.');
    } finally { setLoading(false); }
  }, [filters]);

  useEffect(() => { load(); }, [load]);
  useAutoRefresh(load, 30000);

  const loadSources = useCallback(async () => {
    setSourcesLoading(true); setSourcesErr('');
    try {
      const [cats, emps, avail] = await Promise.all([
        inventoryService.categories(),
        inventoryService.employees(),
        inventoryService.items({ status: 'available' }),
      ]);
      setCategories(cats); setEmployees(emps); setAssignables(avail);
    } catch (e) {
      setSourcesErr(e?.response?.data?.detail || e.message || 'Could not load assets/employees.');
    } finally { setSourcesLoading(false); }
  }, []);
  useEffect(() => { loadSources(); }, [loadSources]);

  const departments = useMemo(() => {
    const m = new Map();
    employees.forEach(u => { if (u.department_ref && u.department_name) m.set(u.department_ref, u.department_name); });
    return [...m.entries()];
  }, [employees]);

  const filteredItems = useMemo(() => {
    const s = itemQ.trim().toLowerCase();
    return s ? assignables.filter(i => `${i.asset_code} ${i.name}`.toLowerCase().includes(s)) : assignables;
  }, [itemQ, assignables]);
  const empOptions = (q) => {
    const s = q.trim().toLowerCase();
    return s ? employees.filter(u => `${u.full_name} ${u.employee_id || ''}`.toLowerCase().includes(s)) : employees;
  };

  const doAssign = async () => {
    if (!assign.item || !assign.assigned_to) return;
    setSaving(true);
    try {
      const { item, ...payload } = assign;
      await inventoryService.assignItem(item, payload);
      setAssign({ item: '', assigned_to: '', assigned_date: today(), handover_condition: 'good', accessories: '', note: '' });
      setItemQ(''); setEmpQ('');
      await load();
      inventoryService.items({ status: 'available' }).then(setAssignables).catch(() => {});
      setPopup({ type: 'success', msg: 'Asset assigned.' });
    } catch (e) {
      setPopup({ type: 'error', msg: e?.response?.data?.detail || 'Could not assign the asset.' });
    } finally { setSaving(false); }
  };

  const doReturn = async () => {
    setSaving(true);
    try { await inventoryService.returnItem(returnCtx.item, returnForm); setReturnCtx(null); await load(); setPopup({ type: 'success', msg: 'Asset returned.' }); }
    catch (e) { setPopup({ type: 'error', msg: e?.response?.data?.detail || 'Could not return.' }); }
    finally { setSaving(false); }
  };

  const doHandover = async () => {
    if (!handoverForm.assigned_to) return;
    setSaving(true);
    try { await inventoryService.handoverItem(handoverCtx.item, handoverForm); setHandoverCtx(null); await load(); setPopup({ type: 'success', msg: 'Asset handed over.' }); }
    catch (e) { setPopup({ type: 'error', msg: e?.response?.data?.detail || 'Could not hand over.' }); }
    finally { setSaving(false); }
  };

  // Hooks must run unconditionally (before any early return) — react-hooks/rules-of-hooks.
  const grouped = useMemo(() => {
    if (!groupByEmp) return null;
    const g = new Map();
    rows.forEach(r => {
      const k = r.assigned_to_name || '—';
      if (!g.has(k)) g.set(k, []);
      g.get(k).push(r);
    });
    return [...g.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [rows, groupByEmp]);

  if (!isManager) {
    return <div className="page"><EmptyState variant="denied" title={EMPTY.noPermission} /></div>;
  }

  const ActionBtns = ({ r }) => (
    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
      <button className="btn btn-ghost btn-sm" title="Handover" onClick={() => { setHandoverForm({ assigned_to: '', assigned_date: today(), handover_condition: r.handover_condition || 'good', note: '' }); setHoEmpQ(''); setHandoverCtx(r); }}><Repeat size={14} /></button>
      <button className="btn btn-ghost btn-sm" title="Return" onClick={() => { setReturnForm({ return_condition: r.handover_condition || 'good', return_remarks: '' }); setReturnCtx(r); }}><RotateCcw size={14} /></button>
    </div>
  );

  return (
    <div className="page">
      {popup && (
        <div className="popup-overlay" onClick={() => setPopup(null)}>
          <div className="popup-modal" onClick={e => e.stopPropagation()}>
            <h3>{popup.type === 'success' ? 'Done' : 'Error'}</h3><p>{popup.msg}</p>
            <button className="popup-btn" onClick={() => setPopup(null)}>OK</button>
          </div>
        </div>
      )}

      <div className="pg-head">
        <div className="pg-head-left">
          <div className="pg-breadcrumb"><button className="pg-back" aria-label="Back" onClick={() => navigate('/inventory')}><ArrowLeft size={18} /></button>Inventory</div>
          <div className="pg-title">Asset Assignment</div>
          <div className="pg-desc">Assign assets to employees and track who is using what</div>
        </div>
      </div>

      {/* Assign panel */}
      <div className="table-card" style={{ padding: 20, marginBottom: 16 }}>
        <div className="tc-title" style={{ marginBottom: 12 }}><UserPlus size={16} style={{ verticalAlign: -3, marginRight: 6 }} />Assign an Asset</div>
        {sourcesErr && (
          <div style={{ background: 'var(--bg-main)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 14px', marginBottom: 12, fontSize: 'var(--fs-sm)', color: 'var(--danger,#b91c1c)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
            <span>{sourcesErr}</span>
            <button className="btn btn-ghost btn-sm" onClick={loadSources}>Retry</button>
          </div>
        )}
        <div className="fgrid">
          <div className="fg"><label>Asset <span className="req">*</span></label>
            <Search placeholder="Search asset…" value={itemQ} onChange={setItemQ} />
            <select value={assign.item} disabled={sourcesLoading || assignables.length === 0} onChange={e => setAssign({ ...assign, item: e.target.value })}>
              <option value="">{sourcesLoading ? 'Loading…' : '— Select available asset —'}</option>
              {filteredItems.map(i => <option key={i.id} value={i.id}>{i.asset_code} · {i.name}</option>)}
            </select>
            {!sourcesLoading && assignables.length === 0 && (
              <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-muted)', marginTop: 6 }}>
                No available assets. Add items on the <button onClick={() => navigate('/inventory')} style={{ background: 'none', border: 'none', padding: 0, color: 'var(--brand-blue)', cursor: 'pointer', textDecoration: 'underline', font: 'inherit' }}>Inventory Items</button> page first.
              </div>
            )}
          </div>
          <div className="fg"><label>Employee <span className="req">*</span></label>
            <Search placeholder="Search employee…" value={empQ} onChange={setEmpQ} />
            <select value={assign.assigned_to} disabled={sourcesLoading || employees.length === 0} onChange={e => setAssign({ ...assign, assigned_to: e.target.value })}>
              <option value="">{sourcesLoading ? 'Loading…' : '— Select employee —'}</option>
              {empOptions(empQ).map(u => <option key={u.id} value={u.id}>{u.full_name}{u.employee_id ? ` (${u.employee_id})` : ''}</option>)}
            </select>
            {!sourcesLoading && employees.length === 0 && (
              <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-muted)', marginTop: 6 }}>No active employees to assign to yet. Add people from Administration → Users.</div>
            )}
          </div>
        </div>
        <div className="fgrid">
          <div className="fg"><label>Assign Date</label><input type="date" value={assign.assigned_date} onChange={e => setAssign({ ...assign, assigned_date: e.target.value })} /></div>
          <div className="fg"><label>Condition</label>
            <select value={assign.handover_condition} onChange={e => setAssign({ ...assign, handover_condition: e.target.value })}>
              {CONDITIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select></div>
        </div>
        <div className="fg"><label>Remarks / Accessories</label><input value={assign.accessories} placeholder="Charger, Bag…" onChange={e => setAssign({ ...assign, accessories: e.target.value })} /></div>
        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
          <button className="btn btn-primary" disabled={saving || !assign.item || !assign.assigned_to} onClick={doAssign}>{saving ? 'Assigning…' : 'Assign Asset'}</button>
        </div>
      </div>

      {/* Who has what */}
      <div className="table-card">
        <div className="wf-head">
          <div className="wf-title-wrap">
            <span className="tc-title">Who Has What</span>
            <span className="wf-count">{rows.length}</span>
          </div>
          <div className="wf-filters">
            <label className="wf-field">
              <span>Employee</span>
              <select className="wf-select" value={filters.employee} onChange={e => setFilters({ ...filters, employee: e.target.value })}>
                <option value="">All employees</option>
                {employees.map(u => <option key={u.id} value={u.id}>{u.full_name}</option>)}
              </select>
            </label>
            <label className="wf-field">
              <span>Department</span>
              <select className="wf-select" value={filters.department} onChange={e => setFilters({ ...filters, department: e.target.value })}>
                <option value="">All departments</option>
                {departments.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
              </select>
            </label>
            <label className="wf-field">
              <span>Category</span>
              <select className="wf-select" value={filters.category} onChange={e => setFilters({ ...filters, category: e.target.value })}>
                <option value="">All categories</option>
                {categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </label>
            <button className={`ftag wf-toggle ${groupByEmp ? 'on' : ''}`} onClick={() => setGroupByEmp(g => !g)}>
              <Users size={14} /> By Employee
            </button>
          </div>
        </div>

        {loading ? (
          <Skeleton rows={4} label="Loading assignments" />
        ) : error ? (
          <div style={{ padding: 40, textAlign: 'center' }}><div style={{ color: 'var(--danger,#b91c1c)', marginBottom: 12 }}>{error}</div><button className="btn btn-primary" onClick={load}>Retry</button></div>
        ) : rows.length === 0 ? (
          <div className="wf-empty">
            <div className="wf-empty-icon"><Package size={30} /></div>
            <div className="wf-empty-title">No assets currently assigned</div>
            <div className="wf-empty-sub">Assign an asset above to start tracking who's using what.</div>
          </div>
        ) : groupByEmp ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 22, padding: '18px 24px 24px' }}>
            {grouped.map(([emp, list]) => (
              <div key={emp}>
                <div className="wf-group-head"><Who name={emp} /><span className="wf-group-count">· {list.length} asset(s)</span></div>
                <div className="resp-table-wrap">
                  <table className="resp-table wf-table"><thead><tr><th>Asset</th><th>Category</th><th>Since</th><th>Condition</th><th>Actions</th></tr></thead>
                    <tbody>{list.map(r => (
                      <tr key={r.id}>
                        <td data-label="Asset"><strong>{r.item_code}</strong> · {r.item_name}<div className="leave-meta" style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-muted)' }}>{r.spec}</div></td>
                        <td data-label="Category">{r.category_name || '—'}</td>
                        <td data-label="Since">{r.assigned_date_bs || r.assigned_date || '—'}</td>
                        <td data-label="Condition"><Cond value={r.handover_condition} label={r.handover_condition_display} /></td>
                        <td data-label="Actions"><ActionBtns r={r} /></td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="resp-table-wrap">
            <table className="resp-table wf-table">
              <thead><tr><th>Asset Code</th><th>Name / Spec</th><th>Category</th><th>Assigned To</th><th>Department</th><th>Since (BS·AD)</th><th>Condition</th><th>Actions</th></tr></thead>
              <tbody>
                {rows.map(r => (
                  <tr key={r.id}>
                    <td data-label="Asset Code"><strong>{r.item_code}</strong></td>
                    <td data-label="Name / Spec">{r.item_name}<div className="leave-meta" style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-muted)' }}>{r.spec || r.asset_type}</div></td>
                    <td data-label="Category">{r.category_name || '—'}</td>
                    <td data-label="Assigned To"><Who name={r.assigned_to_name} /></td>
                    <td data-label="Department">{r.department_name || '—'}</td>
                    <td data-label="Since">{r.assigned_date_bs ? `${r.assigned_date_bs} · ${r.assigned_date}` : (r.assigned_date || '—')}</td>
                    <td data-label="Condition"><Cond value={r.handover_condition} label={r.handover_condition_display} /></td>
                    <td data-label="Actions"><ActionBtns r={r} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Return modal */}
      {returnCtx && (
        <div className="modal-overlay" onClick={() => !saving && setReturnCtx(null)}>
          <div className="modal-content" style={{ maxWidth: 460 }} onClick={e => e.stopPropagation()}>
            <h3 style={{ fontSize: 'var(--fs-h2)' }}>Return “{returnCtx.item_name}”</h3>
            <p style={{ color: 'var(--text-muted)', fontSize: 'var(--fs-sm)', margin: '0 0 16px' }}>{returnCtx.item_code} · from {returnCtx.assigned_to_name}</p>
            <div className="fg"><label>Return condition</label>
              <select value={returnForm.return_condition} onChange={e => setReturnForm({ ...returnForm, return_condition: e.target.value })}>
                {CONDITIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select></div>
            <div className="fg"><label>Remarks</label><textarea rows={2} style={{ minHeight: 60 }} value={returnForm.return_remarks} onChange={e => setReturnForm({ ...returnForm, return_remarks: e.target.value })} /></div>
            <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end' }}>
              <button className="btn btn-ghost" disabled={saving} onClick={() => setReturnCtx(null)}>Cancel</button>
              <button className="btn btn-primary" disabled={saving} onClick={doReturn}>{saving ? 'Returning…' : 'Return to Stock'}</button>
            </div>
          </div>
        </div>
      )}

      {/* Handover modal */}
      {handoverCtx && (
        <div className="modal-overlay" onClick={() => !saving && setHandoverCtx(null)}>
          <div className="modal-content" style={{ maxWidth: 520 }} onClick={e => e.stopPropagation()}>
            <h3 style={{ fontSize: 'var(--fs-h2)' }}>Handover “{handoverCtx.item_name}”</h3>
            <p style={{ color: 'var(--text-muted)', fontSize: 'var(--fs-sm)', margin: '0 0 16px' }}>{handoverCtx.item_code} · currently: {handoverCtx.assigned_to_name}</p>
            <div className="fg"><label>Hand over to <span className="req">*</span></label>
              <Search placeholder="Search employee…" value={hoEmpQ} onChange={setHoEmpQ} />
              <select value={handoverForm.assigned_to} onChange={e => setHandoverForm({ ...handoverForm, assigned_to: e.target.value })}>
                <option value="">— Select employee —</option>
                {empOptions(hoEmpQ).map(u => <option key={u.id} value={u.id}>{u.full_name}{u.employee_id ? ` (${u.employee_id})` : ''}</option>)}
              </select>
            </div>
            <div className="fgrid">
              <div className="fg"><label>Date</label><input type="date" value={handoverForm.assigned_date} onChange={e => setHandoverForm({ ...handoverForm, assigned_date: e.target.value })} /></div>
              <div className="fg"><label>Condition</label>
                <select value={handoverForm.handover_condition} onChange={e => setHandoverForm({ ...handoverForm, handover_condition: e.target.value })}>
                  {CONDITIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select></div>
            </div>
            <div className="fg"><label>Remarks</label><textarea rows={2} style={{ minHeight: 60 }} value={handoverForm.note} onChange={e => setHandoverForm({ ...handoverForm, note: e.target.value })} /></div>
            <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end' }}>
              <button className="btn btn-ghost" disabled={saving} onClick={() => setHandoverCtx(null)}>Cancel</button>
              <button className="btn btn-primary" disabled={saving || !handoverForm.assigned_to} onClick={doHandover}>{saving ? 'Working…' : 'Hand Over'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default AssetAssignment;
