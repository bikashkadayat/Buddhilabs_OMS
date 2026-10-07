import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { Plus, Pencil, UserPlus, RotateCcw, Repeat } from 'lucide-react';
import { useAuth } from '../../hooks/useAuth';
import { useAutoRefresh } from '../../hooks/useAutoRefresh';
import { useBodyScrollLock } from '../../hooks/useBodyScrollLock';
import { inventoryService, assetLifecycle } from '../../services/inventoryService';
import PageHeader from '../../components/common/PageHeader';
import StatusBadge from '../../components/common/StatusBadge';
import EmptyState from '../../components/common/EmptyState';
import Skeleton from '../../components/common/Skeleton';
import { FormSection, FormRow, FormActions } from '../../components/common/Form';

/**
 * Phase D1. The nine lifecycle statuses now render through the shared
 * StatusBadge, so an asset's state looks the same here as it does in the work
 * queue and on Home. The local colour table and its inline styles are gone; the
 * status VALUES and their server-side meanings are untouched.
 */
const StatusPill = ({ value, label }) => <StatusBadge status={value} label={label} />;

// Readers of the whole register. Write controls follow the SERVER's
// can_manage_assets (see canWrite below), so the Board sees no Add/Edit/Assign.
const MANAGER_ROLES = ['admin', 'bod', 'approver', 'checker'];
const CONDITIONS = [['new', 'New'], ['good', 'Good'], ['fair', 'Fair'], ['damaged', 'Damaged']];
const ASSET_TYPES = [['it', 'IT / Computing'], ['peripheral', 'Peripheral'], ['furniture', 'Furniture'], ['vehicle', 'Vehicle'], ['other', 'Other']];
const IT_TYPES = ['it', 'peripheral'];
const today = () => new Date().toISOString().slice(0, 10);

const emptyItem = {
  name: '', asset_type: 'it', category: '', serial_number: '', condition: 'good',
  purchase_date: '', notes: '', status: 'available',
  brand: '', model: '', cpu: '', ram: '', storage_type: '', storage_size: '',
  gpu: '', screen_size: '', os: '', mac_address: '', ip_address: '',
  warranty_expiry: '', purchase_cost: '', vendor: '', accessories: '',
  // Phase 70.4 asset master.
  location: '', warranty_start: '', document_name: '',
  // Phase ASSET-LIFECYCLE-DISPOSAL: what depreciation needs, and the service
  // contract that outlives the warranty.
  useful_life_months: '', salvage_value: '',
  amc_provider: '', amc_contract_number: '', amc_start: '', amc_end: '', amc_cost: '',
};

/**
 * The keys that are files rather than values (Phase 70.4).
 *
 * These never go into the JSON payload: a File serialised as JSON arrives at the
 * server as the string "[object File]", which the magic-byte validator then
 * refuses with a message about content not matching the extension - a confusing
 * way to be told the request was built wrong. When either is set the whole payload
 * is sent as multipart instead.
 */
const FILE_KEYS = ['photo', 'document'];

/**
 * The status filter row (Phase 70.2).
 *
 * The nine lifecycle statuses in the order an asset passes through them, so the
 * row reads as the life of an asset rather than as an alphabetical list. Labelled
 * explicitly rather than title-casing the stored value: "procurement" is what the
 * database calls it, "On Order" is what the storekeeper calls it.
 */
const STATUS_FILTERS = [
  ['all', 'All'],
  ['procurement', 'On Order'],
  ['received', 'Awaiting Check-In'],
  ['available', 'Available'],
  ['assigned', 'Assigned'],
  ['out', 'Taken Out'],
  ['maintenance', 'Maintenance'],
  ['retired', 'Retired'],
  ['disposed', 'Disposed'],
  ['archived', 'Archived'],
];
const FILTER_VALUES = STATUS_FILTERS.map(([value]) => value);

/**
 * Filter value -> key in the /inventory/dashboard/ counts payload (Phase D1).
 *
 * Needed because the two vocabularies were written for different readers: the
 * register filters by the stored status ("out", "procurement"), while the
 * dashboard reports in the storekeeper's words ("taken_out", "on_order"). A
 * value missing here simply shows no number rather than showing a wrong one -
 * `archived` has no aggregate on the server, and inventing one from the current
 * page would be worse than leaving the chip bare.
 */
const COUNT_KEYS = {
  all: 'total_assets',
  procurement: 'on_order',
  received: 'awaiting_check_in',
  available: 'available',
  assigned: 'assigned',
  out: 'taken_out',
  maintenance: 'maintenance',
  retired: 'retired',
  disposed: 'disposed',
};

/**
 * The statuses an asset can be REGISTERED at.
 *
 * Two, not nine. Everything else is somewhere the lifecycle puts an asset -
 * assigned, out, in maintenance, disposed - and offering those here would invite
 * somebody to declare an asset assigned without anyone being assigned it.
 */
const REGISTRABLE_STATUSES = [
  ['available', 'Available — already on the shelf'],
  ['procurement', 'On Order — not yet delivered'],
];

const toFormData = (payload) => {
  const form = new FormData();
  Object.entries(payload).forEach(([key, value]) => {
    if (value === null || value === undefined || value === '') return;
    if (key === 'specifications') { form.append(key, JSON.stringify(value)); return; }
    form.append(key, value);
  });
  return form;
};

// Searchable native employee <select> (text filter above the list).
const EmployeePicker = ({ employees, value, onChange }) => {
  const [q, setQ] = useState('');
  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    return s ? employees.filter(u => `${u.full_name} ${u.employee_id || ''}`.toLowerCase().includes(s)) : employees;
  }, [q, employees]);
  return (
    <>
      <input placeholder="Search employee…" value={q} onChange={e => setQ(e.target.value)} style={{ marginBottom: 8 }} />
      <select value={value} onChange={e => onChange(e.target.value)}>
        <option value="">— Select employee —</option>
        {filtered.map(u => <option key={u.id} value={u.id}>{u.full_name}{u.employee_id ? ` (${u.employee_id})` : ''}</option>)}
      </select>
    </>
  );
};

const InventoryList = () => {
  const navigate = useNavigate();
  const { role } = useAuth();
  const isManager = MANAGER_ROLES.includes(role);

  const [items, setItems] = useState([]);
  const [categories, setCategories] = useState([]);
  const [employees, setEmployees] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  /*
   * The dashboard tiles link here as `/inventory?status=procurement`. Without
   * reading that back the tile counts something, sends you to this page, and this
   * page shows you everything - the count and the list disagree, and the tile is
   * the one that looks wrong. An unrecognised value falls back to `all` rather
   * than filtering on a status the server has never heard of.
   */
  const [searchParams, setSearchParams] = useSearchParams();
  const requested = searchParams.get('status');
  const [statusFilter, setStatusFilter] = useState(
    FILTER_VALUES.includes(requested) ? requested : 'all');
  /*
   * Phase ASSET-TRANSFER-GOVERNANCE. The brief's other four filters, kept apart
   * from `statusFilter` because that one is a chip row with counts and these are
   * selects. All four are applied SERVER-side for the same reason status is: the
   * page holds one filter's rows, so narrowing in the browser would filter a
   * subset and quietly report fewer matches than exist.
   */
  const [facets, setFacets] = useState({
    department: '', owner: '', asset_type: '', condition: '', search: '' });
  const [popup, setPopup] = useState(null);

  // Keep the URL honest as the filter changes, so the view is shareable and the
  // back button returns to the filter the user came from.
  const chooseStatus = (value) => {
    setStatusFilter(value);
    setSearchParams(value === 'all' ? {} : { status: value }, { replace: true });
  };

  const [editItem, setEditItem] = useState(null);
  const [assignCtx, setAssignCtx] = useState(null);   // { item, mode: 'assign'|'handover' }
  const [assignForm, setAssignForm] = useState({ assigned_to: '', assigned_date: today(), handover_condition: 'good', accessories: '', note: '' });
  const [returnCtx, setReturnCtx] = useState(null);
  const [returnForm, setReturnForm] = useState({ return_condition: 'good', return_remarks: '' });
  const [saving, setSaving] = useState(false);
  useBodyScrollLock(!!(editItem || assignCtx || returnCtx || popup));

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const params = statusFilter === 'all' ? {} : { status: statusFilter };
      Object.entries(facets).forEach(([key, value]) => { if (value) params[key] = value; });
      const [it, cats] = await Promise.all([inventoryService.items(params), inventoryService.categories()]);
      setItems(it); setCategories(cats);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Failed to load inventory.');
    } finally { setLoading(false); }
  }, [statusFilter, facets]);

  /**
   * Phase D1. Register-wide counts for the summary cards and the filter chips.
   *
   * Fetched from /inventory/dashboard/ rather than derived from `items`, because
   * the status filter is applied SERVER-side - `items` holds only the current
   * filter's rows, so counting it would report "Available (12)" while showing
   * the twelve assigned ones. Kept in its own request so changing filter does
   * not refetch it.
   *
   * The endpoint returns two shapes: an organisation view with `counts`, and a
   * personal view with no `counts` key at all. Anything below the register bar
   * simply gets no summary, rather than a crash.
   *
   * Refetched after a registration, an assignment or a return - not on the 30s
   * poll. A stale summary above a fresh list is the failure worth avoiding, and
   * those three are the only things on this page that move a number.
   */
  const [counts, setCounts] = useState(null);
  /*
   * Phase ASSET-TRANSFER-GOVERNANCE. `canWrite` comes from the SERVER's own role
   * summary, not from the role in the token.
   *
   * `isManager` is a client-side guess at what a role may do, and it stopped
   * being true the moment a Department Head became a reader: the register offered
   * them "Add asset", "Edit", "Assign" and "Return", every one of which the server
   * then refused with a 403. Found in the browser - the API was right the whole
   * time and the page was advertising work that could not be done.
   *
   * Null until the summary arrives, so nothing flashes on screen and then
   * disappears; the write controls appear once the answer is known.
   */
  const [canWrite, setCanWrite] = useState(false);
  const loadCounts = useCallback(async () => {
    try {
      const data = await assetLifecycle.dashboard();
      setCounts(data?.scope === 'organisation' ? data.counts : null);
      setCanWrite(Boolean(data?.roles?.can_manage_assets));
    } catch { setCounts(null); setCanWrite(false); }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { loadCounts(); }, [loadCounts]);
  useAutoRefresh(load, 30000);
  useEffect(() => { if (isManager) inventoryService.employees().then(setEmployees).catch(() => {}); }, [isManager]);
  /*
   * Department options for the filter, from the by-department report rather than
   * from the loaded rows: the rows are one filter's worth, so deriving the list
   * from them would drop every department that the current filter happens to
   * exclude - and the option you need would vanish exactly when you reached for
   * it. The report is already readable by anyone who can see the whole register.
   */
  const [departments, setDepartments] = useState([]);
  useEffect(() => {
    if (!isManager) return;
    assetLifecycle.report('by_department')
      .then((d) => setDepartments((d.rows || []).filter((r) => r.department_id)))
      .catch(() => {});
  }, [isManager]);

  const saveItem = async () => {
    setSaving(true);
    try {
      const payload = { ...editItem };
      ['category', 'purchase_date', 'warranty_expiry', 'warranty_start', 'ip_address', 'purchase_cost',
        'useful_life_months', 'salvage_value', 'amc_start', 'amc_end', 'amc_cost',
      ].forEach(k => { if (!payload[k]) delete payload[k]; });
      // A file that came back from the server is a URL string, not something to
      // re-upload; only a freshly picked File is sent.
      FILE_KEYS.forEach(k => { if (!(payload[k] instanceof File)) delete payload[k]; });
      const body = FILE_KEYS.some(k => payload[k]) ? toFormData(payload) : payload;
      if (editItem.id) await inventoryService.updateItem(editItem.id, body);
      else await inventoryService.createItem(body);
      setEditItem(null); await Promise.all([load(), loadCounts()]);
      setPopup({ type: 'success', msg: editItem.id ? 'Item updated.' : 'Item added.' });
    } catch (e) {
      const data = e?.response?.data;
      // Field errors matter more than a generic message here: "File content
      // ('text/html') does not match the '.png' extension" tells the officer what
      // to do next, where "Could not save the item" does not.
      const first = data && typeof data === 'object'
        ? Object.entries(data).map(([k, v]) => `${k}: ${Array.isArray(v) ? v[0] : v}`)[0]
        : null;
      setPopup({ type: 'error', msg: data?.detail || first || 'Could not save the item.' });
    } finally { setSaving(false); }
  };

  const openAssign = (item, mode) => {
    setAssignForm({ assigned_to: '', assigned_date: today(), handover_condition: item.condition || 'good', accessories: item.accessories || '', note: '' });
    setAssignCtx({ item, mode });
  };

  const doAssign = async () => {
    if (!assignForm.assigned_to) return;
    setSaving(true);
    try {
      const fn = assignCtx.mode === 'handover' ? inventoryService.handoverItem : inventoryService.assignItem;
      await fn(assignCtx.item.id, assignForm);
      setAssignCtx(null); await Promise.all([load(), loadCounts()]);
      setPopup({ type: 'success', msg: assignCtx.mode === 'handover' ? 'Item handed over.' : 'Item assigned.' });
    } catch (e) {
      setPopup({ type: 'error', msg: e?.response?.data?.detail || 'Could not complete the action.' });
    } finally { setSaving(false); }
  };

  const doReturn = async () => {
    setSaving(true);
    try {
      await inventoryService.returnItem(returnCtx.id, returnForm);
      setReturnCtx(null); await Promise.all([load(), loadCounts()]);
      setPopup({ type: 'success', msg: 'Item returned to stock.' });
    } catch (e) {
      setPopup({ type: 'error', msg: e?.response?.data?.detail || 'Could not return the item.' });
    } finally { setSaving(false); }
  };

  const showSpecs = IT_TYPES.includes(editItem?.asset_type);

  return (
    <div className="page inv-page">
      {popup && (
        <div className="popup-overlay" onClick={() => setPopup(null)}>
          <div className="popup-modal" onClick={e => e.stopPropagation()}>
            <h3>{popup.type === 'success' ? 'Done' : 'Error'}</h3>
            <p>{popup.msg}</p>
            <button className="popup-btn" onClick={() => setPopup(null)}>OK</button>
          </div>
        </div>
      )}

      <PageHeader
        breadcrumb={<Link to="/assets">Assets</Link>}
        title="Inventory Register"
        description="Manage organisational assets and equipment"
        actions={canWrite && (
          <button className="btn btn-primary" onClick={() => setEditItem({ ...emptyItem })}>
            <Plus size={16} /> Add asset
          </button>
        )}
      />

      {/* Summary. Every figure is a link to the register filtered to it - the
          same "every number is a door" rule the workspace uses. */}
      {counts && (
        <ul className="inv-summary">
          {[
            ['all', 'Total assets', counts.total_assets],
            ['available', 'Available', counts.available],
            ['assigned', 'Assigned', counts.assigned],
            ['maintenance', 'Maintenance', counts.maintenance],
            ['retired', 'Retired', counts.retired],
          ].map(([value, label, n]) => (
            <li key={value}>
              <button
                type="button"
                className={`inv-sum${statusFilter === value ? ' is-on' : ''}`}
                onClick={() => chooseStatus(value)}
                aria-pressed={statusFilter === value}
              >
                <span className="inv-sum-n">{n ?? '—'}</span>
                <span className="inv-sum-l">{label}</span>
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="table-card">
        <div className="tc-top inv-top">
          <span className="tc-title">Items</span>
          {/* Phase D1. Equal height, consistent spacing, and a horizontal
              scroller below the desktop breakpoint rather than a wrapped block
              of chips. Counts come from the register-wide payload, so a chip
              shows the whole register's total rather than the filtered page. */}
          <div className="inv-filters" role="group" aria-label="Filter by status">
            {STATUS_FILTERS.map(([value, label]) => {
              const key = COUNT_KEYS[value];
              const n = counts && key ? counts[key] : undefined;
              const on = statusFilter === value;
              return (
                <button
                  key={value}
                  type="button"
                  aria-pressed={on}
                  className={`inv-chip${on ? ' is-on' : ''}`}
                  onClick={() => chooseStatus(value)}
                >
                  {label}
                  {n !== undefined && n !== null && <span className="inv-chip-n">{n}</span>}
                </button>
              );
            })}
          </div>
        </div>

        {/* Phase ASSET-TRANSFER-GOVERNANCE - the brief's five filters. Status is
            the chip row above; these are the other four, plus a free-text search
            that the server matches against name, code, serial, owner, department
            and category. */}
        <div className="inv-facets" role="group" aria-label="Filter assets">
          <label className="lr-field">
            <span>Department</span>
            <select value={facets.department}
              onChange={(e) => setFacets({ ...facets, department: e.target.value })}>
              <option value="">All departments</option>
              {departments.map((d) => (
                <option key={d.department_id} value={d.department_id}>
                  {d.department} ({d.total})
                </option>
              ))}
            </select>
          </label>
          <label className="lr-field">
            <span>Owner</span>
            <select value={facets.owner}
              onChange={(e) => setFacets({ ...facets, owner: e.target.value })}>
              <option value="">Anyone</option>
              <option value="unassigned">Nobody — unassigned</option>
              {employees.map((u) => (
                <option key={u.id} value={u.id}>{u.full_name}</option>
              ))}
            </select>
          </label>
          <label className="lr-field">
            <span>Asset type</span>
            <select value={facets.asset_type}
              onChange={(e) => setFacets({ ...facets, asset_type: e.target.value })}>
              <option value="">All types</option>
              {ASSET_TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <label className="lr-field">
            <span>Condition</span>
            <select value={facets.condition}
              onChange={(e) => setFacets({ ...facets, condition: e.target.value })}>
              <option value="">Any condition</option>
              {CONDITIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <label className="lr-field">
            <span>Search</span>
            <input type="search" value={facets.search}
              placeholder="Name, code, serial, owner…"
              onChange={(e) => setFacets({ ...facets, search: e.target.value })} />
          </label>
        </div>

        {loading ? (
          <Skeleton rows={5} height={16} label="Loading inventory" />
        ) : error ? (
          <EmptyState variant="error" title="Could not load the register" body={error}
            onAction={load} actionLabel="Try again" />
        ) : (
          <div className="resp-table-wrap">
            <table className="resp-table">
              <thead>
                <tr>
                  <th>Asset Code</th><th>Item</th><th>Category</th>
                  <th>Status</th><th>Current Holder</th>{canWrite && <th>Actions</th>}
                </tr>
              </thead>
              <tbody>
                {items.map(it => (
                  <tr key={it.id}>
                    <td data-label="Asset Code">
                      <button onClick={() => navigate(`/inventory/items/${it.id}`)}
                        style={{ fontWeight: 700, color: 'var(--brand-blue)', background: 'none', border: 'none', padding: 0, cursor: 'pointer', textDecoration: 'underline', font: 'inherit' }}>
                        {it.asset_code}
                      </button>
                    </td>
                    <td data-label="Item">
                      <div>{it.name}</div>
                      <div className="leave-meta" style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-muted)' }}>
                        {it.asset_type_display}{it.brand ? ` · ${it.brand} ${it.model}` : ''}{it.serial_number ? ` · SN ${it.serial_number}` : ''}
                      </div>
                    </td>
                    <td data-label="Category">{it.category_name || '—'}</td>
                    <td data-label="Status"><StatusPill value={it.status} label={it.status_display} /></td>
                    <td data-label="Holder">
                      {it.current_holder
                        ? <div>{it.current_holder}<div className="leave-meta" style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-muted)' }}>since {it.assigned_date_bs || it.assigned_date || '—'}</div></div>
                        : <span style={{ color: 'var(--text-muted)' }}>—</span>}
                    </td>
                    {canWrite && (
                      <td data-label="Actions">
                        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                          <button className="btn btn-ghost btn-sm" title="Edit" onClick={() => setEditItem({
                            ...emptyItem, ...it, category: it.category || '',
                            purchase_date: it.purchase_date || '', warranty_expiry: it.warranty_expiry || '',
                            warranty_start: it.warranty_start || '', location: it.location || '',
                            ip_address: it.ip_address || '', purchase_cost: it.purchase_cost ?? '',
                            useful_life_months: it.useful_life_months ?? '',
                            salvage_value: it.salvage_value ?? '',
                            amc_provider: it.amc_provider || '',
                            amc_contract_number: it.amc_contract_number || '',
                            amc_start: it.amc_start || '', amc_end: it.amc_end || '',
                            amc_cost: it.amc_cost ?? '',
                            effective_useful_life_months: it.effective_useful_life_months ?? '',
                            // The server returns these as URLs. Blanking them keeps
                            // an edit that touches nothing else from re-posting a
                            // string where the field expects a file.
                            photo: '', document: '',
                          })}><Pencil size={14} /></button>
                          {['available', 'assigned'].includes(it.status) && (
                            <button className="btn btn-ghost btn-sm" title="Assign" onClick={() => openAssign(it, 'assign')}><UserPlus size={14} /></button>
                          )}
                          {['assigned', 'out'].includes(it.status) && (
                            <button className="btn btn-ghost btn-sm" title="Handover" onClick={() => openAssign(it, 'handover')}><Repeat size={14} /></button>
                          )}
                          {['assigned', 'out'].includes(it.status) && (
                            <button className="btn btn-ghost btn-sm" title="Return" onClick={() => { setReturnForm({ return_condition: it.condition || 'good', return_remarks: '' }); setReturnCtx(it); }}><RotateCcw size={14} /></button>
                          )}
                        </div>
                      </td>
                    )}
                  </tr>
                ))}
                {items.length === 0 && (
                  <tr className="inv-empty-row">
                    <td colSpan={canWrite ? 6 : 5}>
                      {/* Two different absences, two different answers: a filter
                          that matches nothing is not the same as an empty
                          register, and offering "Add first asset" to somebody
                          who simply filtered to Retired would be wrong. */}
                      {statusFilter === 'all' ? (
                        <EmptyState
                          variant="first"
                          title="You haven’t added any assets yet"
                          body="Add laptops, phones, furniture — anything you lend to staff — and track who has what."
                          {...(canWrite
                            ? { onAction: () => setEditItem({ ...emptyItem }), actionLabel: 'Add first asset' }
                            : {})}
                        />
                      ) : (
                        <EmptyState
                          variant="filtered"
                          title="Nothing at this status"
                          body="No assets are currently in this state."
                          onAction={() => chooseStatus('all')}
                          actionLabel="Show all assets"
                        />
                      )}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Add / Edit modal */}
      {editItem && (
        <div className="modal-overlay" onClick={() => !saving && setEditItem(null)}>
          {/*
            * Phase D1. Same fields, same payload, same validation - regrouped
            * into four titled sections with a scrolling body between a fixed
            * title and a fixed footer, so Save is reachable without scrolling
            * to the bottom of thirty inputs. On a phone the panel becomes a
            * full-height drawer rather than a small box inside a small screen.
            *
            * Every control now goes through FormRow, which is not cosmetic:
            * the old markup put a bare <label> next to its input with no
            * htmlFor and no wrapping, so none of these fields had an
            * accessible name at all.
            */}
          <div className="modal-content inv-modal" role="dialog" aria-modal="true"
            aria-labelledby="inv-edit-title" onClick={e => e.stopPropagation()}>
            <header className="inv-modal-hd">
              <h3 id="inv-edit-title">{editItem.id ? 'Edit asset' : 'Add asset'}</h3>
              <p>{editItem.id
                ? 'Update the register entry. Lifecycle moves are made from the row actions.'
                : 'Register a new asset. Only the name and category are required.'}</p>
            </header>

            <div className="inv-modal-body">
              <FormSection title="Basic information"
                description="What the asset is and how it is identified.">
                <div className="inv-2col">
                  <FormRow label="Name" required
                    hint="How it will appear in the register.">
                    {(f) => <input {...f} value={editItem.name} placeholder="e.g. Dell Latitude 5540"
                      onChange={e => setEditItem({ ...editItem, name: e.target.value })} />}
                  </FormRow>
                  <FormRow label="Asset type">
                    {(f) => <select {...f} value={editItem.asset_type}
                      onChange={e => setEditItem({ ...editItem, asset_type: e.target.value })}>
                      {ASSET_TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                    </select>}
                  </FormRow>
                  <FormRow label="Category" required
                    error={!editItem.category && editItem.name ? 'Choose a category.' : null}>
                    {(f) => <select {...f} value={editItem.category || ''}
                      onChange={e => setEditItem({ ...editItem, category: e.target.value })}>
                      <option value="">— Select category —</option>
                      {categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                    </select>}
                  </FormRow>
                  <FormRow label="Serial number" hint="Leave empty if it has none.">
                    {(f) => <input {...f} value={editItem.serial_number} placeholder="Optional"
                      onChange={e => setEditItem({ ...editItem, serial_number: e.target.value })} />}
                  </FormRow>
                  <FormRow label="Condition">
                    {(f) => <select {...f} value={editItem.condition}
                      onChange={e => setEditItem({ ...editItem, condition: e.target.value })}>
                      {CONDITIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                    </select>}
                  </FormRow>
                  {/*
                    * Settable when REGISTERING an asset - an asset already on the
                    * shelf starts Available, one still on order starts On Order.
                    * Locked afterwards, because from then on the lifecycle actions
                    * are the only thing that moves an asset, and they record who
                    * did it. Disabled rather than hidden so the current status
                    * still reads, with the reason next to it instead of a
                    * rejection after saving.
                    */}
                  <FormRow label="Status"
                    hint={editItem.id
                      ? 'Use the lifecycle actions to move this asset, so the change is recorded against whoever made it.'
                      : null}>
                    {(f) => <select {...f} value={editItem.status} disabled={!!editItem.id}
                      onChange={e => setEditItem({ ...editItem, status: e.target.value })}>
                      {REGISTRABLE_STATUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                      {/* An asset being edited may sit at a status nobody registers at. */}
                      {editItem.id && !REGISTRABLE_STATUSES.some(([v]) => v === editItem.status) && (
                        <option value={editItem.status}>{editItem.status_display || editItem.status}</option>
                      )}
                    </select>}
                  </FormRow>
                </div>
              </FormSection>

              <FormSection title="Asset details"
                description="Where it came from, where it lives, and anything worth recording.">
                <div className="inv-2col">
                  <FormRow label="Purchase date">
                    {(f) => <input {...f} type="date" value={editItem.purchase_date || ''}
                      onChange={e => setEditItem({ ...editItem, purchase_date: e.target.value })} />}
                  </FormRow>
                  <FormRow label="Vendor / supplier">
                    {(f) => <input {...f} value={editItem.vendor}
                      onChange={e => setEditItem({ ...editItem, vendor: e.target.value })} />}
                  </FormRow>
                  <FormRow label="Location" hint="Room, floor or site — where it physically is.">
                    {(f) => <input {...f} value={editItem.location}
                      onChange={e => setEditItem({ ...editItem, location: e.target.value })} />}
                  </FormRow>
                  <FormRow label="Warranty start">
                    {(f) => <input {...f} type="date" value={editItem.warranty_start || ''}
                      onChange={e => setEditItem({ ...editItem, warranty_start: e.target.value })} />}
                  </FormRow>
                </div>
                <FormRow label="Notes">
                  {(f) => <textarea {...f} rows={3} value={editItem.notes}
                    onChange={e => setEditItem({ ...editItem, notes: e.target.value })} />}
                </FormRow>
              </FormSection>

              {/*
                * Phase ASSET-LIFECYCLE-DISPOSAL. Not under Specifications, which
                * is IT-only: a generator and a desk depreciate and can be under
                * contract exactly as a laptop can.
                *
                * Useful life is left blank by default so the category's figure
                * applies. Typing one here overrides it for this asset alone -
                * which is what the hint says, because an empty field that
                * silently means "inherit" is a field people fill in needlessly.
                */}
              <FormSection title="Depreciation & service cover"
                description="What the asset is worth over time, and who maintains it.">
                <div className="inv-2col">
                  <FormRow label="Useful life (months)"
                    hint="Leave empty to use the category's figure.">
                    {(f) => <input {...f} type="number" min="1" step="1"
                      value={editItem.useful_life_months}
                      placeholder={editItem.effective_useful_life_months
                        ? `Category: ${editItem.effective_useful_life_months}` : 'Category default'}
                      onChange={e => setEditItem({ ...editItem, useful_life_months: e.target.value })} />}
                  </FormRow>
                  <FormRow label="Salvage value"
                    hint="What it should still be worth at the end of its life.">
                    {(f) => <input {...f} type="number" min="0" step="0.01"
                      value={editItem.salvage_value}
                      onChange={e => setEditItem({ ...editItem, salvage_value: e.target.value })} />}
                  </FormRow>
                  <FormRow label="AMC provider"
                    hint="Who holds the annual maintenance contract.">
                    {(f) => <input {...f} value={editItem.amc_provider}
                      onChange={e => setEditItem({ ...editItem, amc_provider: e.target.value })} />}
                  </FormRow>
                  <FormRow label="AMC contract number">
                    {(f) => <input {...f} value={editItem.amc_contract_number}
                      onChange={e => setEditItem({ ...editItem, amc_contract_number: e.target.value })} />}
                  </FormRow>
                  <FormRow label="AMC start">
                    {(f) => <input {...f} type="date" value={editItem.amc_start || ''}
                      onChange={e => setEditItem({ ...editItem, amc_start: e.target.value })} />}
                  </FormRow>
                  <FormRow label="AMC end"
                    hint="You are warned 60 days before this date.">
                    {(f) => <input {...f} type="date" value={editItem.amc_end || ''}
                      onChange={e => setEditItem({ ...editItem, amc_end: e.target.value })} />}
                  </FormRow>
                  <FormRow label="AMC cost">
                    {(f) => <input {...f} type="number" min="0" step="0.01"
                      value={editItem.amc_cost}
                      onChange={e => setEditItem({ ...editItem, amc_cost: e.target.value })} />}
                  </FormRow>
                </div>
              </FormSection>

              <FormSection title="Documents"
                description="A photo and the purchase paperwork, if you have them.">
                <div className="inv-2col">
                  <FormRow label="Photo"
                    hint={editItem.id ? 'Leave empty to keep the current photo.' : 'PNG or JPG.'}>
                    {(f) => <input {...f} type="file" accept=".png,.jpg,.jpeg"
                      onChange={e => setEditItem({ ...editItem, photo: e.target.files[0] || '' })} />}
                  </FormRow>
                  <FormRow label="Invoice / warranty card"
                    hint="PDF, image, Word or Excel.">
                    {(f) => <input {...f} type="file" accept=".pdf,.png,.jpg,.jpeg,.docx,.xlsx"
                      onChange={e => setEditItem({ ...editItem, document: e.target.files[0] || '' })} />}
                  </FormRow>
                </div>
                <FormRow label="Document label" hint="What to call the file in the register.">
                  {(f) => <input {...f} value={editItem.document_name} placeholder="e.g. Invoice 2026-114"
                    onChange={e => setEditItem({ ...editItem, document_name: e.target.value })} />}
                </FormRow>
              </FormSection>

              {showSpecs && (
                <FormSection title="Specifications"
                  description="Shown for IT and peripheral assets. All optional.">
                  <div className="inv-2col">
                    <FormRow label="Brand">
                      {(f) => <input {...f} value={editItem.brand} onChange={e => setEditItem({ ...editItem, brand: e.target.value })} />}
                    </FormRow>
                    <FormRow label="Model">
                      {(f) => <input {...f} value={editItem.model} onChange={e => setEditItem({ ...editItem, model: e.target.value })} />}
                    </FormRow>
                    <FormRow label="CPU">
                      {(f) => <input {...f} value={editItem.cpu} placeholder="e.g. Intel i7-1355U" onChange={e => setEditItem({ ...editItem, cpu: e.target.value })} />}
                    </FormRow>
                    <FormRow label="RAM">
                      {(f) => <input {...f} value={editItem.ram} placeholder="e.g. 16GB" onChange={e => setEditItem({ ...editItem, ram: e.target.value })} />}
                    </FormRow>
                    <FormRow label="Storage type">
                      {(f) => <input {...f} value={editItem.storage_type} placeholder="SSD / HDD / NVMe" onChange={e => setEditItem({ ...editItem, storage_type: e.target.value })} />}
                    </FormRow>
                    <FormRow label="Storage size">
                      {(f) => <input {...f} value={editItem.storage_size} placeholder="e.g. 512GB" onChange={e => setEditItem({ ...editItem, storage_size: e.target.value })} />}
                    </FormRow>
                    <FormRow label="GPU">
                      {(f) => <input {...f} value={editItem.gpu} onChange={e => setEditItem({ ...editItem, gpu: e.target.value })} />}
                    </FormRow>
                    <FormRow label="Screen size">
                      {(f) => <input {...f} value={editItem.screen_size} placeholder={'e.g. 15.6"'} onChange={e => setEditItem({ ...editItem, screen_size: e.target.value })} />}
                    </FormRow>
                    <FormRow label="Operating system">
                      {(f) => <input {...f} value={editItem.os} onChange={e => setEditItem({ ...editItem, os: e.target.value })} />}
                    </FormRow>
                    <FormRow label="Accessories">
                      {(f) => <input {...f} value={editItem.accessories} placeholder="Charger, bag, mouse" onChange={e => setEditItem({ ...editItem, accessories: e.target.value })} />}
                    </FormRow>
                    <FormRow label="MAC address">
                      {(f) => <input {...f} value={editItem.mac_address} onChange={e => setEditItem({ ...editItem, mac_address: e.target.value })} />}
                    </FormRow>
                    <FormRow label="IP address">
                      {(f) => <input {...f} value={editItem.ip_address} placeholder="Optional" onChange={e => setEditItem({ ...editItem, ip_address: e.target.value })} />}
                    </FormRow>
                    <FormRow label="Warranty expiry">
                      {(f) => <input {...f} type="date" value={editItem.warranty_expiry || ''} onChange={e => setEditItem({ ...editItem, warranty_expiry: e.target.value })} />}
                    </FormRow>
                    <FormRow label="Purchase cost">
                      {(f) => <input {...f} type="number" step="0.01" value={editItem.purchase_cost} onChange={e => setEditItem({ ...editItem, purchase_cost: e.target.value })} />}
                    </FormRow>
                  </div>
                </FormSection>
              )}
            </div>

            <FormActions>
              <button className="btn btn-ghost" disabled={saving} onClick={() => setEditItem(null)}>Cancel</button>
              <button className="btn btn-primary"
                disabled={saving || !editItem.name.trim() || !editItem.category}
                onClick={saveItem}>{saving ? 'Saving…' : 'Save asset'}</button>
            </FormActions>
          </div>
        </div>
      )}

      {/* Assign / Handover modal */}
      {assignCtx && (
        <div className="modal-overlay" onClick={() => !saving && setAssignCtx(null)}>
          <div className="modal-content" style={{ maxWidth: 520 }} onClick={e => e.stopPropagation()}>
            <h3 style={{ margin: '0 0 4px', fontSize: 'var(--fs-h2)' }}>{assignCtx.mode === 'handover' ? 'Handover' : 'Assign'} “{assignCtx.item.name}”</h3>
            <p style={{ color: 'var(--text-muted)', fontSize: 'var(--fs-sm)', margin: '0 0 16px' }}>
              {assignCtx.item.asset_code}{assignCtx.item.current_holder ? ` · currently: ${assignCtx.item.current_holder}` : ''}
            </p>
            <div className="fg"><label>{assignCtx.mode === 'handover' ? 'Hand over to' : 'Assign to'} <span className="req">*</span></label>
              <EmployeePicker employees={employees} value={assignForm.assigned_to} onChange={v => setAssignForm({ ...assignForm, assigned_to: v })} />
            </div>
            <div className="fgrid">
              <div className="fg"><label>Date</label><input type="date" value={assignForm.assigned_date} onChange={e => setAssignForm({ ...assignForm, assigned_date: e.target.value })} /></div>
              <div className="fg"><label>Condition on handover</label>
                <select value={assignForm.handover_condition} onChange={e => setAssignForm({ ...assignForm, handover_condition: e.target.value })}>
                  {CONDITIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select></div>
            </div>
            <div className="fg"><label>Accessories</label><input value={assignForm.accessories} placeholder="Charger, Bag, Mouse" onChange={e => setAssignForm({ ...assignForm, accessories: e.target.value })} /></div>
            <div className="fg"><label>Remarks</label><textarea rows={2} style={{ minHeight: 60 }} value={assignForm.note} onChange={e => setAssignForm({ ...assignForm, note: e.target.value })} /></div>
            <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end' }}>
              <button className="btn btn-ghost" disabled={saving} onClick={() => setAssignCtx(null)}>Cancel</button>
              <button className="btn btn-primary" disabled={saving || !assignForm.assigned_to} onClick={doAssign}>{saving ? 'Working…' : (assignCtx.mode === 'handover' ? 'Hand Over' : 'Assign')}</button>
            </div>
          </div>
        </div>
      )}

      {/* Return modal */}
      {returnCtx && (
        <div className="modal-overlay" onClick={() => !saving && setReturnCtx(null)}>
          <div className="modal-content" style={{ maxWidth: 460 }} onClick={e => e.stopPropagation()}>
            <h3 style={{ margin: '0 0 4px', fontSize: 'var(--fs-h2)' }}>Return “{returnCtx.name}”</h3>
            <p style={{ color: 'var(--text-muted)', fontSize: 'var(--fs-sm)', margin: '0 0 16px' }}>{returnCtx.asset_code} · from {returnCtx.current_holder || '—'}</p>
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
    </div>
  );
};

export default InventoryList;
