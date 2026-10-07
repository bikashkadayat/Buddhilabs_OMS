import React, { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CheckCircle2, ChevronDown, ChevronUp, Paperclip, Plus, Trash2, XCircle,
} from 'lucide-react';

import { assetLifecycle, inventoryService } from '../../services/inventoryService';
import Toast from '../../components/admin/Toast';
import { Skeleton, ErrorState, EmptyState } from '../../components/leave-records/States';

const MIN_REMARKS = 10;
const MIN_REASON = 10;

const STATUS_TONES = {
  draft: 'muted',
  dept_head_review: 'warn',
  admin_approval: 'warn',
  disposed: 'ok',
  rejected: 'no',
  cancelled: 'muted',
};

const TABS = [
  { key: 'awaiting', label: 'Awaiting my decision', params: { scope: 'awaiting_me' } },
  { key: 'open', label: 'In progress',
    params: { status: 'draft,dept_head_review,admin_approval' } },
  { key: 'disposed', label: 'Disposed', params: { status: 'disposed' } },
  { key: 'all', label: 'All', params: {} },
];

const blankForm = () => ({
  item: '', disposal_type: '', reason: '', expected_proceeds: '', remarks: '',
});

const firstError = (e) => {
  const payload = e?.response?.data;
  if (!payload) return 'The request failed. Check your connection and try again.';
  if (typeof payload === 'string') return payload;
  const value = payload.detail || Object.values(payload)[0];
  return Array.isArray(value) ? String(value[0]) : String(value);
};

const money = (value) => (value === null || value === undefined
  ? '—'
  : `Rs. ${Number(value).toLocaleString('en-IN', { minimumFractionDigits: 2 })}`);

/**
 * Asset disposal (Phase ASSET-LIFECYCLE-DISPOSAL).
 *
 * EVERY BUTTON COMES FROM A SERVER CAPABILITY FLAG, as on Asset Transfer. Who may
 * decide depends on the stage and on whether the viewer raised the request or was
 * holding the asset; `permissions` already answers that, so this file never asks
 * who the user is.
 *
 * NO FIGURE ON THIS PAGE IS CALCULATED HERE. Book value, accumulated
 * depreciation, the amount written off and any gain all arrive computed, because
 * they are accounting figures that have to agree with the reports an auditor
 * reads. Deriving them a second time in the browser would be a second opinion.
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

const DisposalDetail = ({ id, onError, onDone }) => {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['inventory', 'disposal', id],
    queryFn: () => assetLifecycle.disposal(id),
  });
  const attach = useMutation({
    mutationFn: (files) => assetLifecycle.attachToDisposal(id, files),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['inventory', 'disposal', id] });
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
          <div key={row.label}>
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

      <h4 className="memo-panel-title">Evidence</h4>
      {data.attachments.length === 0
        ? <p className="lr-page-sub">
            No documents attached. A disposal is easier to audit with the quotation,
            scrap receipt or police report on file.
          </p>
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

const AssetDisposals = () => {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const openId = params.get('open');
  const presetItem = params.get('item');
  const [tab, setTab] = useState(openId ? 'all' : 'awaiting');
  const [search, setSearch] = useState('');
  const [expanded, setExpanded] = useState(openId);
  const [creating, setCreating] = useState(Boolean(presetItem));
  const [form, setForm] = useState(() => ({ ...blankForm(), item: presetItem || '' }));
  const [remarks, setRemarks] = useState({});
  const [toast, setToast] = useState(null);

  const activeTab = TABS.find((t) => t.key === tab);
  const query = useMemo(() => ({ ...activeTab.params, ...(search ? { q: search } : {}) }),
    [activeTab, search]);

  const { data: rows = [], isLoading, isError, error, refetch } = useQuery({
    queryKey: ['inventory', 'disposals', query],
    queryFn: () => assetLifecycle.disposals(query),
  });
  const { data: options } = useQuery({
    queryKey: ['inventory', 'disposal-options'],
    queryFn: assetLifecycle.disposalOptions,
    staleTime: 10 * 60 * 1000,
  });
  const canCreate = Boolean(options?.can_create);
  const { data: items = [] } = useQuery({
    queryKey: ['inventory', 'items', 'disposable'],
    queryFn: () => inventoryService.items({}),
    enabled: creating && canCreate,
  });

  // A notification link to another disposal can arrive while this page is open.
  const [seenOpenId, setSeenOpenId] = useState(openId);
  if (openId !== seenOpenId) {
    setSeenOpenId(openId);
    if (openId) { setExpanded(openId); setTab('all'); }
  }

  const chosenType = (options?.types || []).find((t) => t.value === form.disposal_type);
  const needsProceeds = Boolean(chosenType?.has_proceeds);

  const done = (message) => {
    setToast({ message, tone: 'success' });
    setRemarks({});
    qc.invalidateQueries({ queryKey: ['inventory'] });
  };
  const fail = (e) => setToast({ message: firstError(e), tone: 'error' });

  const create = useMutation({
    mutationFn: () => assetLifecycle.createDisposal({
      ...form,
      expected_proceeds: needsProceeds && form.expected_proceeds !== ''
        ? form.expected_proceeds : null,
    }),
    onSuccess: (disposal) => {
      setCreating(false);
      setForm(blankForm());
      setExpanded(disposal.id);
      setTab('open');
      done(`${disposal.disposal_number} saved as a draft. Submit it to start approval.`);
    },
    onError: fail,
  });

  const act = useMutation({
    mutationFn: ({ id, verb }) => {
      const payload = { remarks: remarks[id] || '' };
      return {
        submit: () => assetLifecycle.submitDisposal(id),
        approve: () => assetLifecycle.approveDisposal(id, payload),
        reject: () => assetLifecycle.rejectDisposal(id, payload),
        cancel: () => assetLifecycle.cancelDisposal(id, payload),
      }[verb]();
    },
    onSuccess: (disposal, { verb }) => done({
      submit: `${disposal.disposal_number} submitted for approval.`,
      approve: disposal.status === 'disposed'
        ? `${disposal.disposal_number} approved. ${disposal.item.asset_code} is off the books and stays on file with its full history.`
        : `${disposal.disposal_number} approved. It is now at ${disposal.status_label}.`,
      reject: `${disposal.disposal_number} rejected. The asset stays on the books.`,
      cancel: `${disposal.disposal_number} cancelled.`,
    }[verb]),
    onError: fail,
  });

  const formReady = form.item && form.disposal_type
    && form.reason.trim().length >= MIN_REASON
    && (!needsProceeds || form.expected_proceeds !== '');
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
          <h1 className="lr-page-title">Asset Disposal</h1>
          <p className="lr-page-sub">
            Taking an asset off the books. Approved by the owning department&apos;s
            head, then an administrator. Nothing is ever deleted — a disposed asset
            stays on file, marked disposed, with its whole history.
          </p>
        </div>
        {canCreate && (
          <button type="button" className="lr-btn lr-btn-primary"
            onClick={() => setCreating((v) => !v)}>
            <Plus size={14} /> New disposal
          </button>
        )}
      </div>

      {creating && (
        <div className="memo-panel">
          <h3 className="memo-panel-title">New Disposal</h3>
          <div className="inv-transfer-form">
            <label className="lr-field">
              <span>Asset *</span>
              <select value={form.item}
                onChange={(e) => setForm({ ...form, item: e.target.value })}>
                <option value="">— choose an asset —</option>
                {items.map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.asset_code} · {row.name}
                    {row.current_holder ? ` (held by ${row.current_holder})` : ''}
                  </option>
                ))}
              </select>
            </label>
            <label className="lr-field">
              <span>Disposal type *</span>
              <select value={form.disposal_type}
                onChange={(e) => setForm({ ...form, disposal_type: e.target.value })}>
                <option value="">— how is it leaving? —</option>
                {(options?.types || []).map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </label>
            {needsProceeds && (
              <label className="lr-field">
                <span>Expected proceeds (Rs.) *</span>
                <input type="number" min="0" step="0.01" value={form.expected_proceeds}
                  onChange={(e) => setForm({ ...form, expected_proceeds: e.target.value })}
                  placeholder="What the sale is expected to raise" />
              </label>
            )}
          </div>
          <label className="lr-field">
            <span>Reason *</span>
            <textarea rows={3} value={form.reason}
              onChange={(e) => setForm({ ...form, reason: e.target.value })}
              placeholder="Why this asset should leave the books — condition, repair quotes, incident reference." />
          </label>
          <label className="lr-field">
            <span>Remarks</span>
            <textarea rows={2} value={form.remarks}
              onChange={(e) => setForm({ ...form, remarks: e.target.value })}
              placeholder="Anything the approvers should know." />
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
          aria-label="Search disposals"
          placeholder="Search by number or asset"
          onChange={(e) => setSearch(e.target.value)} />
      </div>

      {isLoading && <Skeleton rows={3} />}
      {isError && <ErrorState error={error} onRetry={refetch} />}
      {!isLoading && !isError && rows.length === 0 && (
        <EmptyState ctaTo={null} message={tab === 'awaiting'
          ? 'Nothing is waiting for your decision.'
          : 'No disposals match.'} />
      )}

      {rows.map((row) => {
        const p = row.permissions;
        const isOpen = expanded === row.id;
        const f = row.figures;
        return (
          <div key={row.id} className="memo-panel" data-testid="asset-disposal">
            <div className="memo-matrix-head">
              <h3 className="memo-panel-title" style={{ margin: 0 }}>
                <Trash2 size={15} aria-hidden="true" /> {row.disposal_number}
              </h3>
              <span className={`min-status is-${STATUS_TONES[row.status] || 'muted'}`}>
                {row.status_label}
              </span>
            </div>

            <dl className="memo-doc-meta">
              <div><dt>Asset</dt><dd>{row.item.asset_code} · {row.item.name}</dd></div>
              <div><dt>Department</dt><dd>{row.item.department || '—'}</dd></div>
              <div><dt>Type</dt><dd>{row.disposal_type_label}</dd></div>
              <div><dt>Requested by</dt><dd>{row.requested_by || '—'}</dd></div>
              {row.last_holder && (
                <div><dt>Last holder</dt><dd>{row.last_holder}</dd></div>)}
              {row.expected_proceeds !== null && (
                <div><dt>Expected proceeds</dt><dd>{money(row.expected_proceeds)}</dd></div>)}
            </dl>

            <p className="lr-page-sub">{row.reason}</p>

            {/* In review: today's book value, so an approver knows what they are
                signing away. Disposed: the figures frozen at that moment. */}
            {f ? (
              <dl className="memo-doc-meta">
                <div><dt>Purchase cost</dt><dd>{money(f.purchase_cost)}</dd></div>
                <div><dt>Accumulated depreciation</dt><dd>{money(f.accumulated)}</dd></div>
                <div><dt>Book value at disposal</dt><dd>{money(f.book_value)}</dd></div>
                <div><dt>Proceeds</dt><dd>{money(f.proceeds)}</dd></div>
                <div><dt>Written off</dt><dd>{money(f.written_off)}</dd></div>
                {Number(f.gain) > 0 && (
                  <div><dt>Gain on disposal</dt><dd>{money(f.gain)}</dd></div>)}
              </dl>
            ) : (
              <p className="lr-page-sub">
                {row.book_value_now !== null
                  ? <>Book value today: <strong>{money(row.book_value_now)}</strong></>
                  : row.book_value_note}
              </p>
            )}

            <StageTrack stages={row.stages} />

            {row.rejection && (
              <div className="memo-notice is-warn" role="status">
                <XCircle size={14} aria-hidden="true" />
                Rejected by {row.rejection.by} at {row.rejection.stage_label}: {row.rejection.remarks}
              </div>
            )}
            {row.remarks && <p className="lr-page-sub">“{row.remarks}”</p>}

            {(p.can_approve || p.can_reject || p.can_cancel) && (
              <label className="lr-field">
                <span>Remarks{p.can_reject ? ' (required to reject)' : ''}</span>
                <textarea rows={2} value={note(row.id)}
                  aria-label={`Remarks for ${row.disposal_number}`}
                  onChange={(e) => setRemarks({ ...remarks, [row.id]: e.target.value })} />
              </label>
            )}

            <div className="memo-matrix-actions">
              {p.can_submit && (
                <button type="button" className="lr-btn lr-btn-primary"
                  disabled={act.isPending}
                  onClick={() => act.mutate({ id: row.id, verb: 'submit' })}>
                  Submit for approval
                </button>
              )}
              {p.can_approve && (
                <button type="button" className="lr-btn lr-btn-primary"
                  disabled={act.isPending}
                  onClick={() => act.mutate({ id: row.id, verb: 'approve' })}>
                  <CheckCircle2 size={14} />
                  {row.status === 'admin_approval' ? ' Approve and dispose' : ' Approve'}
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
                  Cancel request
                </button>
              )}
              <button type="button" className="lr-btn lr-btn-ghost"
                aria-expanded={isOpen} onClick={() => toggle(row.id)}>
                {isOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                {isOpen ? ' Hide details' : ' Details'}
              </button>
            </div>
            {row.status === 'admin_approval' && p.can_approve && (
              <p className="lr-page-sub">
                Final approval takes {row.item.asset_code} off the books immediately
                and cannot be undone. The record and its history stay on file.
              </p>
            )}

            {isOpen && <DisposalDetail id={row.id} onError={fail} onDone={done} />}
          </div>
        );
      })}

      {toast && <Toast {...toast} onClose={() => setToast(null)} />}
    </div>
  );
};

export default AssetDisposals;
