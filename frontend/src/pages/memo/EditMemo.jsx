import React, { useCallback, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Save } from 'lucide-react';
import { memoService } from '../../services/memoService';
import MemoSectionsEditor from '../../components/memo/MemoSectionsEditor';
import { useAuth } from '../../hooks/useAuth';
import { useDocumentDraft } from '../../hooks/useDocumentDraft';
import useExitGuard from '../../hooks/useExitGuard';
import SaveIndicator from '../../components/drafts/SaveIndicator';
import DraftRecoveryDialog from '../../components/drafts/DraftRecoveryDialog';
import VersionHistory from '../../components/drafts/VersionHistory';
import { RESTRICTED_TYPES } from '../../components/memo/memoLabels';
import { MemoStatusBadge } from '../../components/memo/badges';
import {
  MEMO_TYPES,
} from '../../components/memo/memoLabels';
import Toast from '../../components/admin/Toast';
import { Skeleton, ErrorState } from '../../components/leave-records/States';

/**
 * Edit a draft or rejected memo.
 *
 * This page had no counterpart before, and the API behind it silently discarded
 * every PATCH (update actions were routed through a serializer whose fields were
 * all read-only). Both are fixed; the server still refuses an edit once the memo
 * is moving through its workflow, or once it is archived.
 */
const EditMemo = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  // Only the user's edits live in state; the untouched fields are read straight
  // from the fetched memo. Seeding a full form copy from an effect instead would
  // mean a setState during render for every load, and would quietly go stale if
  // the memo were refetched underneath the page.
  const [edits, setEdits] = useState({});
  const [toast, setToast] = useState(null);
  const { user } = useAuth();

  const { data: memo, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['memo', id], queryFn: () => memoService.getMemo(id),
  });

  // Memoised because the autosave snapshot is keyed off it: a fresh object on
  // every render would make the snapshot recompute continuously.
  const form = useMemo(() => (memo ? {
    subject: memo.subject || '',
    to_line: memo.to_line || '',
    memo_type: memo.memo_type || 'general',
    reference_number: memo.reference_number || '',
    ...edits,
  } : null), [memo, edits]);

  // Sections are their own endpoint, so they are edited and saved separately from
  // the header - the manual's edit form shows both on one page (p.5), which is
  // what the two save calls below produce.
  const sections = useMemo(() => (memo
    ? (edits.sections || (memo.sections || []).map(
      ({ title, body }) => ({ title, body })))
    : []), [memo, edits.sections]);

  /* ---------------------------------------------------------------- *
   * Autosave (Phase 111.13) — the edit path, keyed to this memo's id so a
   * recovered snapshot can never be applied to a different document.
   * ---------------------------------------------------------------- */
  const draftPayload = useMemo(
    () => (form ? { form, sections } : undefined), [form, sections],
  );

  const draft = useDocumentDraft({
    kind: 'memo',
    documentKey: id,
    payload: draftPayload,
    userId: user?.id,
    // Withheld until the memo has loaded, or the empty form would be
    // snapshotted over the real content on open.
    enabled: Boolean(user?.id) && Boolean(memo),
    confidential: RESTRICTED_TYPES.includes(form?.memo_type),
  });

  useExitGuard(draft.unsaved);

  const applySnapshot = useCallback((payload) => {
    if (!payload) return;
    setEdits((prev) => ({
      ...prev,
      ...(payload.form || {}),
      ...(Array.isArray(payload.sections) ? { sections: payload.sections } : {}),
    }));
  }, []);

  const save = useMutation({
    mutationFn: async () => {
      const { sections: _drop, ...header } = form;
      await memoService.updateMemo(id, header);
      await memoService.setSections(id, sections.map((s, position) => ({
        position, title: s.title, body: s.body || '',
      })));
    },
    onSuccess: async () => {
      qc.invalidateQueries({ queryKey: ['memo', id] });
      qc.invalidateQueries({ queryKey: ['memos'] });
      // Committed, so the snapshot goes.
      await draft.complete();
      navigate(`/memos/${id}`);
    },
    onError: (e) => setToast({
      message: e?.response?.data?.detail || 'Could not save your changes.',
      tone: 'error',
    }),
  });

  if (isLoading || form === null) return <div className="page"><Skeleton rows={4} /></div>;
  if (isError) return <div className="page"><ErrorState error={error} onRetry={refetch} /></div>;

  if (!memo.can_edit) {
    return (
      <div className="page memo-page">
        <button type="button" className="lr-btn lr-btn-ghost memo-back" onClick={() => navigate(`/memos/${id}`)}>
          <ArrowLeft size={14} /> Back to memo
        </button>
        <div className="memo-notice" role="status">
          This memo can no longer be edited — it is <MemoStatusBadge status={memo.status} />.
        </div>
      </div>
    );
  }

  const set = (key) => (e) => setEdits((prev) => ({ ...prev, [key]: e.target.value }));
  const valid = form.to_line.trim() && form.subject.trim();

  return (
    <div className="page memo-page memo-create">
      {draft.recoverable && (
        <DraftRecoveryDialog
          draft={draft.recoverable}
          label="memo"
          onRestore={() => { applySnapshot(draft.recoverable.payload); draft.acceptRecovery(); }}
          onContinue={draft.dismissRecovery}
          onDiscard={draft.discard}
        />
      )}

      <button type="button" className="lr-btn lr-btn-ghost memo-back" onClick={() => navigate(`/memos/${id}`)}>
        <ArrowLeft size={14} /> Back to memo
      </button>

      <div className="lr-page-head">
        <div>
          <h2>Edit Memo</h2>
          <div className="lr-page-sub">{memo.memo_number} · <MemoStatusBadge status={memo.status} /></div>
        </div>
        <SaveIndicator status={draft.status} lastSavedAt={draft.lastSavedAt} />
      </div>

      <div className="memo-panel">
        <label className="lr-field">
          <span>To <span aria-hidden>*</span></span>
          <input value={form.to_line} onChange={set('to_line')} aria-label="To"
            placeholder="The approver's functional title, e.g. CEO" />
        </label>
        <label className="lr-field">
          <span>Subject <span aria-hidden>*</span></span>
          <input value={form.subject} onChange={set('subject')} aria-label="Subject" />
        </label>
        <div className="memo-field-row">
          <label className="lr-field">
            <span>Memo Type</span>
            <select value={form.memo_type} onChange={set('memo_type')}
              aria-label="Memo Type">
              {MEMO_TYPES.map((type) => (
                <option key={type.code} value={type.code}>{type.label}</option>
              ))}
            </select>
          </label>
          <label className="lr-field">
            <span>Reference Memo</span>
            <input value={form.reference_number} onChange={set('reference_number')}
              aria-label="Reference Memo"
              placeholder="The reference code of a related memo" />
          </label>
        </div>
      </div>

      <div className="memo-panel">
        <MemoSectionsEditor sections={sections}
          onChange={(next) => setEdits((prev) => ({ ...prev, sections: next }))} />
      </div>


      <VersionHistory kind="memo" documentKey={id} versions={draft.versions}
        onRestored={(payload) => { applySnapshot(payload); draft.refreshVersions(); }} />
      <div className="memo-actionbar">
        <button type="button" className="lr-btn lr-btn-ghost" onClick={() => navigate(`/memos/${id}`)}>Cancel</button>
        <button type="button" className="lr-btn lr-btn-primary" disabled={!valid || save.isPending}
          onClick={() => save.mutate()}>
          <Save size={14} /> {save.isPending ? 'Saving…' : 'Save changes'}
        </button>
      </div>

      {toast && <Toast {...toast} onClose={() => setToast(null)} />}
    </div>
  );
};

export default EditMemo;
