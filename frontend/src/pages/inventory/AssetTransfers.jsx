import React, { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowRight, CheckCircle2, ChevronDown, ChevronUp, Paperclip, Plus, Repeat, XCircle,
} from 'lucide-react';

import { assetLifecycle, inventoryService } from '../../services/inventoryService';
import AssetPicker from '../../components/inventory/AssetPicker';
import Toast from '../../components/admin/Toast';
import { Skeleton, ErrorState, EmptyState } from '../../components/leave-records/States';

const MIN_REMARKS = 10;

const STATUS_TONES = {
  draft: 'muted',
  hr_review: 'warn',
  completed: 'ok',
  rejected: 'no',
  cancelled: 'muted',
  // Retired gates (Phase ASSET-TRANSFER-GOVERNANCE). Kept so a transfer decided
  // under the old three-stage workflow still renders with a tone rather than
  // falling through to 'muted' and reading as if nothing had happened.
  dept_head_review: 'warn',
  admin_approval: 'warn',
};

const TABS = [
  { key: 'awaiting', label: 'Awaiting my decision', params: { scope: 'awaiting_me' } },
  { key: 'open', label: 'In progress',
    // The retired stages stay in the filter: a database that has not run
    // migration 0011 can still hold transfers resting at one, and dropping them
    // from the query would hide the very records that need attention.
    params: { status: 'draft,hr_review,dept_head_review,admin_approval' } },
  { key: 'completed', label: 'Completed', params: { status: 'completed' } },
  { key: 'all', label: 'All', params: {} },
];

const blankForm = () => ({
  asset: null, to_employee: '', transfer_date: new Date().toISOString().slice(0, 10),
  reason: '', condition: '', remarks: '',
});

const firstError = (e) => {
  const payload = e?.response?.data;
  if (!payload) return 'The request failed. Check your connection and try again.';
  if (typeof payload === 'string') return payload;
  const value = payload.detail || Object.values(payload)[0];
  return Array.isArray(value) ? String(value[0]) : String(value);
};

/**
 * Asset custody transfers (Phase ASSET-CUSTODY-TRANSFER).
 *
 * EVERY BUTTON COMES FROM A SERVER CAPABILITY FLAG, exactly as on Asset Requests.
 * Who may decide depends on the stage AND on whether the viewer is giving,
 * receiving or raising the transfer; `permissions.can_approve` already answers
 * that, so this file never asks who the user is.
 *
 * The stage chips come from `stages` on the payload too. Submission is an event
 * rather than a place a transfer rests, and the server says so; re-deriving the
 * chain here would draw a sixth stage nobody is responsible for.
 */
const StageTrack = ({ stages = [] }) => (
  <div className="inv-progress" aria-label="Approval stages">
    {stages.map((stage) => (
      <span key={stage.key}
        className={`inv-progress-step${stage.state === 'done' ? ' is-done' : ''}${
          stage.state === 'rejected' ? ' is-rejected' : ''}${
          stage.state === 'active' ? ' is-active' : ''}`}
        title={stage.by ? `${stage.label} — ${stage.by}` : stage.label}>
        {stage.label}
      </span>
    ))}
  </div>
);

const TransferDetail = ({ id, onError, onDone }) => {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['inventory', 'transfer', id],
    queryFn: () => assetLifecycle.transfer(id),
  });
  const attach = useMutation({
    mutationFn: (files) => assetLifecycle.attachToTransfer(id, files),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['inventory', 'transfer', id] });
      onDone('Attachment added.');
    },
    onError,
  });

  if (isLoading || !data) return <Skeleton rows={2} />;
  return (
    <div className="inv-transfer-detail">
      <h4 className="memo-panel-title">Approvals</h4>
      <dl className="memo-doc-meta">
        {data.stage_approvals.map((row) => (
          <div key={row.stage}>
            <dt>{row.label}</dt>
            <dd>
              {row.by
                ? <>{row.by} · {new Date(row.at).toLocaleString()}
                  {row.remarks && <><br /><span className="lr-page-sub">“{row.remarks}”</span></>}</>
                : <span className="lr-page-sub">Not yet decided</span>}
            </dd>
          </div>
        ))}
      </dl>

      <h4 className="memo-panel-title">Attachments</h4>
      {data.attachments.length === 0
        ? <p className="lr-page-sub">No documents attached.</p>
        : (
          <ul className="inv-transfer-files">
            {data.attachments.map((file) => (
              <li key={file.id}>
                <a href={file.url} target="_blank" rel="noreferrer">
                  <Paperclip size={13} aria-hidden="true" /> {file.name}
                </a>
                <span className="lr-page-sub"> · {file.uploaded_by}</span>
              </li>
            ))}
          </ul>
        )}
      {data.permissions.can_attach && (
        <label className="lr-field">
          <span>Add a document</span>
          <input type="file" multiple disabled={attach.isPending}
            onChange={(e) => e.target.files.length && attach.mutate(e.target.files)} />
        </label>
      )}

      <h4 className="memo-panel-title">History</h4>
      <ol className="inv-transfer-timeline">
        {data.timeline.map((event) => (
          <li key={event.sequence}>
            <strong>{event.label}</strong>
            <span className="lr-page-sub"> — {event.actor} · {new Date(event.at).toLocaleString()}</span>
            {event.remarks && <div className="lr-page-sub">“{event.remarks}”</div>}
          </li>
        ))}
      </ol>
    </div>
  );
};

const AssetTransfers = () => {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const openId = params.get('open');
  const [tab, setTab] = useState(openId ? 'all' : 'awaiting');
  const [search, setSearch] = useState('');
  const [expanded, setExpanded] = useState(openId);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState(blankForm);
  const [remarks, setRemarks] = useState({});
  const [toast, setToast] = useState(null);

  const activeTab = TABS.find((t) => t.key === tab);
  const query = useMemo(() => ({ ...activeTab.params, ...(search ? { q: search } : {}) }),
    [activeTab, search]);

  const { data: rows = [], isLoading, isError, error, refetch } = useQuery({
    queryKey: ['inventory', 'transfers', query],
    queryFn: () => assetLifecycle.transfers(query),
  });
  const { data: options } = useQuery({
    queryKey: ['inventory', 'transfer-options'],
    queryFn: assetLifecycle.transferOptions,
    staleTime: 10 * 60 * 1000,
  });
  const canCreate = Boolean(options?.can_create);
  const { data: employees = [] } = useQuery({
    queryKey: ['inventory', 'employees'],
    queryFn: inventoryService.employees,
    enabled: creating && canCreate,
    staleTime: 5 * 60 * 1000,
  });

  // A notification link to another transfer can arrive while this page is open.
  // Adjusting state during render (rather than in an effect) is how React wants a
  // changed prop mirrored: one render, no cascade.
  const [seenOpenId, setSeenOpenId] = useState(openId);
  if (openId !== seenOpenId) {
    setSeenOpenId(openId);
    if (openId) { setExpanded(openId); setTab('all'); }
  }

  const selectedItem = form.asset;

  const done = (message) => {
    setToast({ message, tone: 'success' });
    setRemarks({});
    qc.invalidateQueries({ queryKey: ['inventory'] });
  };
  const fail = (e) => setToast({ message: firstError(e), tone: 'error' });

  const create = useMutation({
    mutationFn: () => {
      const { asset, ...rest } = form;
      return assetLifecycle.createTransfer({
        ...rest, item: asset?.id, to_employee: form.to_employee || null,
      });
    },
    onSuccess: (transfer) => {
      setCreating(false);
      setForm(blankForm());
      setExpanded(transfer.id);
      setTab('open');
      done(`${transfer.transfer_number} saved as a draft. Submit it to start approval.`);
    },
    onError: fail,
  });

  const act = useMutation({
    mutationFn: ({ id, verb }) => {
      const payload = { remarks: remarks[id] || '' };
      return {
        submit: () => assetLifecycle.submitTransfer(id),
        approve: () => assetLifecycle.approveTransfer(id, payload),
        reject: () => assetLifecycle.rejectTransfer(id, payload),
        cancel: () => assetLifecycle.cancelTransfer(id, payload),
      }[verb]();
    },
    onSuccess: (transfer, { verb }) => done({
      submit: `${transfer.transfer_number} submitted for approval.`,
      approve: transfer.status === 'completed'
        ? `${transfer.transfer_number} approved and completed. Custody has moved to ${transfer.to_employee?.name}.`
        : `${transfer.transfer_number} approved. It is now at ${transfer.status_label}.`,
      reject: `${transfer.transfer_number} rejected. The asset stays where it is.`,
      cancel: `${transfer.transfer_number} cancelled.`,
    }[verb]),
    onError: fail,
  });

  const formReady = form.asset && form.to_employee && form.reason && form.condition
    && form.transfer_date;
  const note = (id) => remarks[id] || '';
  const toggle = (id) => {
    const next = expanded === id ? null : id;
    setExpanded(next);
    if (!next && openId) setParams({}, { replace: true });
  };

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">Asset Transfer</h1>
          <p className="lr-page-sub">
            Moving an asset from one employee to another. HR approves, and custody
            changes the moment they do.
          </p>
        </div>
        {canCreate && (
          <button type="button" className="lr-btn lr-btn-primary"
            onClick={() => setCreating((v) => !v)}>
            <Plus size={14} /> New transfer
          </button>
        )}
      </div>

      {creating && (
        <div className="memo-panel">
          <h3 className="memo-panel-title">New Transfer</h3>
          {/* Phase ASSET-TRANSFER-GOVERNANCE. The asset is chosen first, because
              everything else on this form is read off the record it returns. */}
          <AssetPicker
            value={form.asset}
            onChange={(asset) => setForm({
              ...form,
              asset,
              // Seed the handover condition from the register, so the usual case
              // is one click. Still editable: the point of recording it at
              // handover is that it may have changed since anybody last looked.
              condition: asset?.condition || form.condition,
              to_employee: '',
            })}
            params={{ status: 'assigned' }}
            emptyHint="No assigned asset matches. Only an asset somebody is holding can be transferred."
          />
          <div className="inv-transfer-form">
            <label className="lr-field">
              <span>To employee *</span>
              <select value={form.to_employee}
                onChange={(e) => setForm({ ...form, to_employee: e.target.value })}>
                <option value="">— choose who receives it —</option>
                {employees
                  .filter((person) => String(person.id) !== String(selectedItem?.current_holder_id))
                  .map((person) => (
                    <option key={person.id} value={person.id}>
                      {person.full_name || person.name}
                      {person.department_name ? ` — ${person.department_name}` : ''}
                    </option>
                  ))}
              </select>
            </label>
            <label className="lr-field">
              <span>Transfer date *</span>
              <input type="date" value={form.transfer_date}
                onChange={(e) => setForm({ ...form, transfer_date: e.target.value })} />
            </label>
            <label className="lr-field">
              <span>Reason *</span>
              <select value={form.reason}
                onChange={(e) => setForm({ ...form, reason: e.target.value })}>
                <option value="">— choose a reason —</option>
                {(options?.reasons || []).map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </label>
            <label className="lr-field">
              <span>Asset condition *</span>
              <select value={form.condition}
                onChange={(e) => setForm({ ...form, condition: e.target.value })}>
                <option value="">— condition at handover —</option>
                {(options?.conditions || []).map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </label>
          </div>
          {form.condition === 'lost' && (
            <div className="memo-notice is-warn" role="status">
              <XCircle size={14} aria-hidden="true" />
              A lost asset cannot be handed to another employee. You can save this
              draft, but it cannot be submitted.
            </div>
          )}
          <label className="lr-field">
            <span>Remarks</span>
            <textarea rows={3} value={form.remarks}
              onChange={(e) => setForm({ ...form, remarks: e.target.value })}
              placeholder="Anything the approvers should know — accessories, damage, dates." />
          </label>
          <div className="memo-matrix-actions">
            <button type="button" className="lr-btn lr-btn-primary"
              disabled={!formReady || create.isPending}
              onClick={() => create.mutate()}>
              {create.isPending ? 'Saving…' : 'Save as draft'}
            </button>
            <button type="button" className="lr-btn lr-btn-ghost"
              onClick={() => { setCreating(false); setForm(blankForm()); }}>
              Cancel
            </button>
          </div>
        </div>
      )}

      <div className="inv-transfer-toolbar">
        <div className="lr-tabs" role="tablist">
          {TABS.map((t) => (
            <button key={t.key} type="button" role="tab" aria-selected={tab === t.key}
              className={`lr-tab${tab === t.key ? ' on' : ''}`}
              onClick={() => setTab(t.key)}>
              {t.label}
            </button>
          ))}
        </div>
        <input type="search" className="inv-transfer-search" value={search}
          aria-label="Search transfers"
          placeholder="Search by number, asset or employee"
          onChange={(e) => setSearch(e.target.value)} />
      </div>

      {isLoading && <Skeleton rows={3} />}
      {isError && <ErrorState error={error} onRetry={refetch} />}
      {!isLoading && !isError && rows.length === 0 && (
        <EmptyState ctaTo={null} message={tab === 'awaiting'
          ? 'Nothing is waiting for your decision.'
          : 'No transfers match.'} />
      )}

      {rows.map((row) => {
        const p = row.permissions;
        const deciding = p.can_approve || p.can_reject;
        const isOpen = expanded === row.id;
        return (
          <div key={row.id} className="memo-panel" data-testid="asset-transfer">
            <div className="memo-matrix-head">
              <h3 className="memo-panel-title" style={{ margin: 0 }}>
                <Repeat size={15} aria-hidden="true" /> {row.transfer_number}
              </h3>
              <span className={`min-status is-${STATUS_TONES[row.status] || 'muted'}`}>
                {row.status_label}
              </span>
            </div>

            <div className="inv-transfer-route">
              <span>
                <strong>{row.from_employee?.name || '—'}</strong>
                {row.from_employee?.department && (
                  <span className="lr-page-sub"> · {row.from_employee.department}</span>)}
              </span>
              <ArrowRight size={16} aria-label="to" />
              <span>
                <strong>{row.to_employee?.name || '—'}</strong>
                {row.to_employee?.department && (
                  <span className="lr-page-sub"> · {row.to_employee.department}</span>)}
              </span>
            </div>

            <dl className="memo-doc-meta">
              <div><dt>Asset</dt><dd>{row.item.asset_code} · {row.item.name}</dd></div>
              <div><dt>Reason</dt><dd>{row.reason_label}</dd></div>
              <div><dt>Condition</dt><dd>{row.condition_label}</dd></div>
              <div><dt>Transfer date</dt>
                <dd>{row.transfer_date}{row.transfer_date_bs && ` · ${row.transfer_date_bs} BS`}</dd></div>
              <div><dt>Requested by</dt><dd>{row.requested_by?.name || '—'}</dd></div>
              {row.approved_by && <div><dt>Approved by</dt><dd>{row.approved_by.name}</dd></div>}
            </dl>

            <StageTrack stages={row.stages} />

            {row.rejection && (
              <div className="memo-notice is-warn" role="status">
                <XCircle size={14} aria-hidden="true" />
                Rejected by {row.rejection.by} at {row.rejection.stage_label}: {row.rejection.remarks}
              </div>
            )}
            {row.remarks && <p className="lr-page-sub">“{row.remarks}”</p>}

            {(deciding || p.can_cancel) && (
              <label className="lr-field">
                <span>Remarks{p.can_reject ? ' (required to reject)' : ''}</span>
                <textarea rows={2} value={note(row.id)}
                  aria-label={`Remarks for ${row.transfer_number}`}
                  onChange={(e) => setRemarks({ ...remarks, [row.id]: e.target.value })} />
              </label>
            )}

            <div className="memo-matrix-actions">
              {p.can_submit && (
                <button type="button" className="lr-btn lr-btn-primary"
                  disabled={act.isPending || row.condition === 'lost'}
                  onClick={() => act.mutate({ id: row.id, verb: 'submit' })}>
                  Submit for approval
                </button>
              )}
              {p.can_approve && (
                <button type="button" className="lr-btn lr-btn-primary"
                  disabled={act.isPending}
                  onClick={() => act.mutate({ id: row.id, verb: 'approve' })}>
                  <CheckCircle2 size={14} />
                  {' Approve and complete'}
                </button>
              )}
              {p.can_reject && (
                <button type="button" className="lr-btn lr-btn-danger"
                  disabled={act.isPending || note(row.id).trim().length < MIN_REMARKS}
                  title={note(row.id).trim().length < MIN_REMARKS
                    ? `Give a reason of at least ${MIN_REMARKS} characters to reject` : undefined}
                  onClick={() => act.mutate({ id: row.id, verb: 'reject' })}>
                  <XCircle size={14} /> Reject
                </button>
              )}
              {p.can_cancel && (
                <button type="button" className="lr-btn lr-btn-ghost"
                  disabled={act.isPending}
                  onClick={() => act.mutate({ id: row.id, verb: 'cancel' })}>
                  Cancel transfer
                </button>
              )}
              <button type="button" className="lr-btn lr-btn-ghost"
                aria-expanded={isOpen} onClick={() => toggle(row.id)}>
                {isOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                {isOpen ? ' Hide details' : ' Details'}
              </button>
            </div>
            {p.can_approve && (
              <p className="lr-page-sub">
                Approving moves custody immediately: {row.from_employee?.name}&apos;s
                assignment closes and {row.to_employee?.name}&apos;s opens.
              </p>
            )}

            {isOpen && <TransferDetail id={row.id} onError={fail} onDone={done} />}
          </div>
        );
      })}

      {toast && <Toast {...toast} onClose={() => setToast(null)} />}
    </div>
  );
};

export default AssetTransfers;
