import React, { useCallback, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Download, Save, Send } from 'lucide-react';

import { circularService } from '../../services/circularService';
import RichTextEditor from '../../components/memo/RichTextEditor';
import { useAuth } from '../../hooks/useAuth';
import { useDocumentDraft } from '../../hooks/useDocumentDraft';
import useExitGuard from '../../hooks/useExitGuard';
import SaveIndicator from '../../components/drafts/SaveIndicator';
import DraftRecoveryDialog from '../../components/drafts/DraftRecoveryDialog';
import ApprovalMatrixEditor from '../../components/memo/ApprovalMatrixEditor';
import Toast from '../../components/admin/Toast';
import { Skeleton, ErrorState } from '../../components/leave-records/States';

const emptyForm = {
  subject: '',
  content: '',
  category: 'administrative',
  classification: 'internal',
  priority: 'normal',
  issue_date: '',
  external_reference: '',
  acknowledgement_required: false,
  acknowledgement_due_days: 7,
};

/**
 * Create and edit a circular (Phase 50).
 *
 * EVERY DROPDOWN COMES FROM THE SERVER. Categories, classifications, priorities and
 * the chain's role types are served by /circulars/taxonomy/, so adding one in the
 * backend appears here with no frontend change.
 *
 * THE CHAIN EDITOR IS THE MEMO'S. ApprovalMatrixEditor already does drag-to-reorder,
 * employee search, role change and add/remove; it takes its role vocabulary as a
 * prop, which is exactly what the minute module already relies on. Reusing it means
 * a circular chain cannot drift from a memo matrix in behaviour, and the one rule
 * that IS different - exactly one issuer, and it must be last - is enforced on the
 * server where it belongs rather than duplicated here.
 */
const CircularForm = ({ mode = 'create' }) => {
  const { id } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const isEdit = mode === 'edit';

  const [form, setForm] = useState(emptyForm);
  const [chain, setChain] = useState([]);
  const [toast, setToast] = useState(null);
  const { user } = useAuth();

  const { data: taxonomy } = useQuery({
    queryKey: ['circulars', 'taxonomy'],
    queryFn: circularService.getTaxonomy,
    staleTime: 10 * 60 * 1000,
  });

  const { data: existing, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['circular', id],
    queryFn: () => circularService.getCircular(id),
    enabled: isEdit,
  });

  // Seed the form from the server once per payload, when editing. Done during
  // render when the payload differs from the one last seeded from - the same
  // condition the old [existing] effect ran on - so the form never renders one
  // frame empty after the data has already arrived.
  const [seededFrom, setSeededFrom] = useState(null);
  if (existing && existing !== seededFrom) {
    setSeededFrom(existing);
    setForm({
      subject: existing.subject || '',
      content: existing.content || '',
      category: existing.category || 'administrative',
      classification: existing.classification || 'internal',
      priority: existing.priority || 'normal',
      issue_date: existing.issue_date || '',
      external_reference: existing.external_reference || '',
      acknowledgement_required: Boolean(existing.acknowledgement_required),
      acknowledgement_due_days: existing.acknowledgement_due_days ?? 7,
    });
    setChain((existing.workflow_steps || []).map((step) => ({
      assignee_id: String(step.assignee?.id),
      full_name: step.assignee?.full_name || step.name,
      designation: step.designation || '',
      department: step.department_label || '',
      role_type: step.role_type,
    })));
  }

  const fail = (e) => {
    const payload = e?.response?.data;
    const message = payload?.detail || payload?.workflow || payload?.subject
      || payload?.content || payload?.import
      || (typeof payload === 'string' && payload.length < 300 && !payload.includes('<') ? payload : null) || 'That didn’t go through. Please try again.';
    setToast({ message: Array.isArray(message) ? message[0] : String(message),
      tone: 'error' });
  };

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['circulars'] });
    if (id) qc.invalidateQueries({ queryKey: ['circular', id] });
  };

  const payload = () => ({
    ...form,
    issue_date: form.issue_date || null,
    acknowledgement_due_days: Number(form.acknowledgement_due_days) || 7,
  });

  /* ---------------------------------------------------------------- *
   * Autosave (Phase 111.15)
   *
   * Subject, content, the reference pulled in from a memo or minute, the
   * audience/classification settings and the review chain — the whole form is
   * one snapshot, so a part-written circular survives the same events a memo
   * does.
   * ---------------------------------------------------------------- */
  const draftPayload = useMemo(() => ({ form, chain }), [form, chain]);

  const draft = useDocumentDraft({
    kind: 'circular',
    documentKey: isEdit ? id : 'new',
    payload: draftPayload,
    userId: user?.id,
    // Withheld until the server data has seeded the form when editing, or the
    // empty form would be snapshotted over the real content on open.
    enabled: Boolean(user?.id) && (!isEdit || Boolean(existing)),
  });

  useExitGuard(draft.unsaved);

  const applySnapshot = useCallback((payload) => {
    if (!payload) return;
    if (payload.form) setForm((prev) => ({ ...prev, ...payload.form }));
    if (Array.isArray(payload.chain)) setChain(payload.chain);
  }, []);

  const save = useMutation({
    mutationFn: () => (isEdit
      ? circularService.updateCircular(id, payload())
      : circularService.createCircular(payload())),
    onSuccess: (row) => {
      invalidate();
      // Committed, so the snapshot goes: otherwise reopening the form offers to
      // restore content that is already saved.
      draft.complete();
      setToast({ message: 'Circular saved.', tone: 'success' });
      if (!isEdit) navigate(`/circulars/${row.id}/edit`, { replace: true });
    },
    onError: fail,
  });

  const saveChain = useMutation({
    mutationFn: (circularId) => circularService.setChain(
      circularId, chain.map((row) => ({ assignee_id: row.assignee_id,
        role_type: row.role_type }))),
    onSuccess: () => { invalidate(); setToast({ message: 'Chain saved.', tone: 'success' }); },
    onError: fail,
  });

  const submit = useMutation({
    mutationFn: async () => {
      // Save content, then the chain, then submit. Sequential rather than
      // concurrent: the server refuses a submit with no chain, and a race would
      // surface that refusal as a confusing error on a circular the user did in
      // fact give a chain to.
      const row = isEdit ? await circularService.updateCircular(id, payload())
        : await circularService.createCircular(payload());
      await circularService.setChain(row.id, chain.map((r) => ({
        assignee_id: r.assignee_id, role_type: r.role_type })));
      return circularService.sendForReview(row.id);
    },
    onSuccess: (row) => {
      invalidate();
      draft.complete();
      navigate(`/circulars/${row.id}`);
    },
    onError: fail,
  });

  const importFrom = useMutation({
    mutationFn: (source) => circularService.importFrom(id, source),
    onSuccess: (row) => {
      setForm((f) => ({ ...f, content: row.content, subject: row.subject }));
      setToast({ message: 'Content imported. Edit it before issuing.',
        tone: 'success' });
      invalidate();
    },
    onError: fail,
  });

  if (isEdit && isLoading) return <div className="page"><Skeleton rows={5} /></div>;
  if (isEdit && isError) {
    return <div className="page"><ErrorState error={error} onRetry={refetch} /></div>;
  }
  if (isEdit && existing && !existing.can_edit) {
    return (
      <div className="page memo-page">
        <div className="memo-notice" role="status">
          This circular is {existing.status_label?.toLowerCase()} and its content is
          the official text. It can be viewed and exported but no longer changed.
        </div>
        <button type="button" className="lr-btn"
          onClick={() => navigate(`/circulars/${id}`)}>Open the circular</button>
      </div>
    );
  }

  const set = (field) => (e) => setForm({
    ...form,
    [field]: e.target.type === 'checkbox' ? e.target.checked : e.target.value,
  });

  return (
    <div className="page memo-page memo-create">
      {draft.recoverable && (
        <DraftRecoveryDialog
          draft={draft.recoverable}
          label="circular"
          onRestore={() => { applySnapshot(draft.recoverable.payload); draft.acceptRecovery(); }}
          onContinue={draft.dismissRecovery}
          onDiscard={draft.discard}
        />
      )}

      <button type="button" className="lr-btn lr-btn-ghost memo-back"
        onClick={() => navigate(-1)}>
        <ArrowLeft size={14} /> Back
      </button>

      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">
            {isEdit ? 'Edit Circular' : 'Create Circular'}
          </h1>
          <p className="lr-page-sub">
            {isEdit && existing
              ? existing.circular_number
              : 'A circular number is allocated when you save'}
          </p>
        </div>
        <SaveIndicator status={draft.status} lastSavedAt={draft.lastSavedAt} />
      </div>

      <div className="memo-create-grid">
        <div className="memo-create-main">
          <div className="memo-panel">
            <h3 className="memo-panel-title">Circular Details</h3>
            <label className="lr-field">
              <span>Subject *</span>
              <input value={form.subject} maxLength={500} onChange={set('subject')}
                placeholder="e.g. Revised office hours from the first of next month" />
            </label>
            <div className="fgrid">
              <label className="lr-field">
                <span>Category *</span>
                <select value={form.category} onChange={set('category')}>
                  {(taxonomy?.categories || []).map((row) => (
                    <option key={row.code} value={row.code}>{row.label}</option>
                  ))}
                </select>
              </label>
              <label className="lr-field">
                <span>Classification *</span>
                <select value={form.classification} onChange={set('classification')}>
                  {(taxonomy?.classifications || []).map((row) => (
                    <option key={row.code} value={row.code}>{row.label}</option>
                  ))}
                </select>
              </label>
              <label className="lr-field">
                <span>Priority</span>
                <select value={form.priority} onChange={set('priority')}>
                  {(taxonomy?.priorities || []).map((row) => (
                    <option key={row.code} value={row.code}>{row.label}</option>
                  ))}
                </select>
              </label>
              <label className="lr-field">
                <span>Issue Date</span>
                <input type="date" value={form.issue_date} onChange={set('issue_date')} />
              </label>
            </div>
            <label className="lr-field">
              <span>Reference</span>
              <input value={form.external_reference} maxLength={100}
                onChange={set('external_reference')}
                placeholder="Board decision, directive or incoming letter" />
            </label>
          </div>

          {isEdit && (
            <div className="memo-panel">
              <h3 className="memo-panel-title">Import Content</h3>
              <p className="lr-page-sub">
                Pull the text of a memo or minute this circular announces. It is
                copied, not linked — editing the source afterwards will not change
                what the circular says, because the version people are told about
                must be the version that was broadcast.
              </p>
              <div className="memo-matrix-actions">
                <button type="button" className="lr-btn"
                  onClick={() => {
                    const memoId = window.prompt('Memo ID to import from');
                    if (memoId) importFrom.mutate({ memo_id: memoId.trim() });
                  }}>
                  <Download size={14} /> Import from memo
                </button>
                <button type="button" className="lr-btn"
                  onClick={() => {
                    const minuteId = window.prompt('Minute ID to import from');
                    if (minuteId) importFrom.mutate({ minute_id: minuteId.trim() });
                  }}>
                  <Download size={14} /> Import from minute
                </button>
              </div>
            </div>
          )}

          <div className="memo-panel">
            <h3 className="memo-panel-title">Content</h3>
            <RichTextEditor value={form.content}
              onChange={(html) => setForm((f) => ({ ...f, content: html }))} />
          </div>

          <div className="memo-panel">
            <h3 className="memo-panel-title">Acknowledgement</h3>
            <p className="lr-page-sub">
              Most circulars are information. Ask for an acknowledgement when you
              need a recorded confirmation from each recipient — it is separate from
              read tracking, which happens either way.
            </p>
            <label className="lr-field lr-field-inline">
              <input type="checkbox" checked={form.acknowledgement_required}
                onChange={set('acknowledgement_required')} />
              <span>Acknowledgement required</span>
            </label>
            {form.acknowledgement_required && (
              <label className="lr-field">
                <span>Due within (days of broadcast)</span>
                <input type="number" min={1} max={90}
                  value={form.acknowledgement_due_days}
                  onChange={set('acknowledgement_due_days')} />
              </label>
            )}
          </div>

          <div className="memo-panel">
            <h3 className="memo-panel-title">Review and Issue</h3>
            <p className="lr-page-sub">
              Reviewers are optional. An issuer is not: a circular carries somebody's
              authority, and they must be the last step.
            </p>
            <ApprovalMatrixEditor
              rows={chain}
              onChange={setChain}
              roles={taxonomy?.role_types}
              roleKey="role_type" />
          </div>
        </div>

        <aside className="memo-create-side">
          <div className="memo-panel memo-actions-panel">
            <h3 className="memo-panel-title">Actions</h3>
            <button type="button" className="lr-btn"
              disabled={save.isPending} onClick={() => save.mutate()}>
              <Save size={14} /> {save.isPending ? 'Saving…' : 'Save draft'}
            </button>
            {isEdit && (
              <button type="button" className="lr-btn lr-btn-ghost"
                disabled={saveChain.isPending}
                onClick={() => saveChain.mutate(id)}>
                Save chain only
              </button>
            )}
            <button type="button" className="lr-btn lr-btn-primary"
              disabled={submit.isPending || !chain.length}
              onClick={() => submit.mutate()}>
              <Send size={14} />
              {submit.isPending ? ' Submitting…' : ' Save and submit'}
            </button>
            {!chain.length && (
              <p className="lr-page-sub">Add an issuer before submitting.</p>
            )}
          </div>
        </aside>
      </div>

      {toast && <Toast {...toast} onClose={() => setToast(null)} />}
    </div>
  );
};

export default CircularForm;
