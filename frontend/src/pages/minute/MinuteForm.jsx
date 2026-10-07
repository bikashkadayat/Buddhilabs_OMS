import React, { useCallback, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, CalendarClock, Save, Search, Send, UserSquare, X } from 'lucide-react';

import { minuteService } from '../../services/minuteService';
import RichTextEditor from '../../components/memo/RichTextEditor';
import { useAuth } from '../../hooks/useAuth';
import { useDocumentDraft } from '../../hooks/useDocumentDraft';
import useExitGuard from '../../hooks/useExitGuard';
import SaveIndicator from '../../components/drafts/SaveIndicator';
import DraftRecoveryDialog from '../../components/drafts/DraftRecoveryDialog';
import EmployeeSelector from '../../components/memo/EmployeeSelector';
import MemberEditor from '../../components/minute/MemberEditor';
import Toast from '../../components/admin/Toast';
import { Skeleton, ErrorState } from '../../components/leave-records/States';

const emptyForm = {
  subject: '', reference_number: '', minute_type: '',
  meeting_date: new Date().toISOString().slice(0, 10),
  meeting_time: '', agenda_body: '',
};

/**
 * Create and edit a minute — the manual's form, field for field (E-minute-manual p.4).
 *
 *     Date*  Time*  Minute Type*
 *     Members Present / Members Absent / Invitee Members
 *     FRO
 *     Reference No + search
 *     Meeting Agenda/Discussion/Decisions
 *
 * The Reference No. search does what p.5 describes: look up a previous meeting's
 * minute and drop its agenda into the editor, to be edited from there.
 *
 * THE EDITOR IS THE MEMO EDITOR. RichTextEditor lazy-loads the same TipTap surface
 * with the same controls, which is what lets the agenda hold the manual's
 * S.N. / Agendas / Responsibility / Deadline table as a real table.
 */
const MinuteForm = ({ mode = 'create' }) => {
  const { id } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const isEdit = mode === 'edit';

  const [form, setForm] = useState(emptyForm);
  const [fro, setFro] = useState(null);
  const [members, setMembers] = useState([]);
  const [lookup, setLookup] = useState('');
  const [toast, setToast] = useState(null);
  const [errors, setErrors] = useState({});
  const { user } = useAuth();

  const { data: taxonomy, isLoading: taxLoading } = useQuery({
    queryKey: ['minutes', 'taxonomy'],
    queryFn: minuteService.getTaxonomy,
    staleTime: 10 * 60 * 1000,
  });

  const { data: existing, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['minute', id],
    queryFn: () => minuteService.getMinute(id),
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
      reference_number: existing.reference_number || '',
      minute_type: existing.minute_type || '',
      meeting_date: existing.meeting_date || '',
      meeting_time: (existing.meeting_time || '').slice(0, 5),
      agenda_body: existing.agenda_body || '',
    });
    setFro(existing.fro || null);
    setMembers((existing.participants || []).map((row) => ({
      user_id: String(row.user?.id),
      full_name: row.name,
      designation: row.designation || '',
      department: row.department_label || '',
      attendance: row.attendance,
    })));
  }

  /*
   * The type falls back to the first active row rather than being written into state
   * by an effect: setState inside an effect causes a cascading re-render, and a
   * derived value cannot go stale against the taxonomy the way a copy in state can.
   */
  const typeId = form.minute_type
    || (!isEdit ? taxonomy?.types?.[0]?.id : '') || '';

  const payload = () => ({
    ...form,
    minute_type: typeId,
    fro_id: fro ? String(fro.id) : null,
  });

  const fail = (e) => {
    const data = e?.response?.data;
    setErrors(data && typeof data === 'object' ? data : {});
    const first = data && typeof data === 'object'
      ? Object.values(data).flat()[0]
      : 'Could not save the minute.';
    setToast({ message: String(first), tone: 'error' });
  };

  const persist = useMutation({
    mutationFn: () => (isEdit
      ? minuteService.updateMinute(id, payload())
      : minuteService.createMinute(payload())),
    onError: fail,
  });

  /**
   * The Reference No. search (p.5). Seeds the agenda from the referenced minute
   * rather than replacing it silently: if there is already content, the fetched text
   * is appended, because losing what somebody typed to a search box is not a
   * behaviour a form should have.
   */
  const search = useMutation({
    mutationFn: () => minuteService.lookupReference(lookup.trim()),
    onSuccess: (found) => {
      setForm((current) => ({
        ...current,
        reference_number: found.minute_number,
        agenda_body: current.agenda_body
          ? `${current.agenda_body}${found.agenda_body || ''}`
          : (found.agenda_body || ''),
      }));
      setToast({ message: `Loaded the agenda from ${found.minute_number}.`,
        tone: 'success' });
    },
    onError: () => setToast({
      message: 'No minute found with that reference.', tone: 'error' }),
  });

  /* ---------------------------------------------------------------- *
   * Autosave (Phase 111.14)
   *
   * Covers what a Minute actually holds: meeting information, agenda body,
   * FRO and the member/attendance list. The Decision, Action and Resolution
   * registers named in the original brief were removed in the PDF 1:1 rebuild
   * and are not modelled, so there is nothing to snapshot for them.
   * ---------------------------------------------------------------- */
  const draftPayload = useMemo(
    () => ({ form, fro, members }), [form, fro, members],
  );

  const draft = useDocumentDraft({
    kind: 'minute',
    documentKey: isEdit ? id : 'new',
    payload: draftPayload,
    userId: user?.id,
    // Held off until the server data has seeded the form, or the empty form
    // would be snapshotted over the real content the moment the page opens.
    enabled: Boolean(user?.id) && (!isEdit || Boolean(existing)),
  });

  useExitGuard(draft.unsaved);

  const applySnapshot = useCallback((payload) => {
    if (!payload) return;
    if (payload.form) setForm((prev) => ({ ...prev, ...payload.form }));
    if (payload.fro !== undefined) setFro(payload.fro);
    if (Array.isArray(payload.members)) setMembers(payload.members);
  }, []);

  const save = async (then) => {
    setErrors({});
    try {
      const saved = await persist.mutateAsync();
      const minuteId = saved.id || id;
      if (members.length) {
        await minuteService.setParticipants(minuteId, members.map((row) => ({
          user_id: row.user_id, attendance: row.attendance,
        })));
      }
      if (then === 'review') await minuteService.sendForReview(minuteId);
      if (then === 'acknowledge') await minuteService.sendForAcknowledgement(minuteId);
      qc.invalidateQueries({ queryKey: ['minutes'] });
      qc.invalidateQueries({ queryKey: ['minute', minuteId] });
      // The document is committed, so the snapshot must go or the next visit
      // offers to restore content that is already saved.
      await draft.complete();
      navigate(`/minutes/${minuteId}`);
    } catch (e) {
      fail(e);
    }
  };

  if (isEdit && isLoading) return <div className="page"><Skeleton rows={5} /></div>;
  if (isEdit && isError) {
    return <div className="page"><ErrorState error={error} onRetry={refetch} /></div>;
  }
  if (taxLoading) return <div className="page"><Skeleton rows={5} /></div>;

  const busy = persist.isPending;
  const canSave = typeId && form.meeting_date && form.meeting_time;
  const presentCount = members.filter((row) => row.attendance === 'present').length;

  return (
    <div className="page memo-page">
      {draft.recoverable && (
        <DraftRecoveryDialog
          draft={draft.recoverable}
          label="minute"
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
            {isEdit ? 'Edit Minute' : 'Create Minute'}
          </h1>
          <p className="lr-page-sub">
            {isEdit
              ? existing?.minute_number
              : 'A minute number is given automatically when you save'}
          </p>
        </div>
        <SaveIndicator status={draft.status} lastSavedAt={draft.lastSavedAt} />
      </div>

      <div className="memo-panel">
        <h3 className="memo-panel-title">
          <CalendarClock size={15} aria-hidden="true" /> Meeting Minute
        </h3>
        <div className="min-form-grid">
          <label className="lr-field">
            <span>Date *</span>
            <input type="date" value={form.meeting_date}
              onChange={(e) => setForm({ ...form, meeting_date: e.target.value })} />
            {errors.meeting_date && (
              <span className="min-err">{errors.meeting_date}</span>
            )}
          </label>
          <label className="lr-field">
            <span>Time *</span>
            <input type="time" value={form.meeting_time}
              onChange={(e) => setForm({ ...form, meeting_time: e.target.value })} />
            {errors.meeting_time && (
              <span className="min-err">{errors.meeting_time}</span>
            )}
          </label>
          <label className="lr-field">
            <span>Minute Type *</span>
            <select value={typeId}
              onChange={(e) => setForm({ ...form, minute_type: e.target.value })}>
              {(taxonomy?.types || []).map((type) => (
                <option key={type.id} value={type.id}>{type.name}</option>
              ))}
            </select>
          </label>
          <label className="lr-field min-span-2">
            <span>Subject</span>
            <input value={form.subject} maxLength={255}
              onChange={(e) => setForm({ ...form, subject: e.target.value })}
              placeholder="Optional — helps find the minute later" />
          </label>
        </div>
      </div>

      <div className="memo-panel">
        <MemberEditor rows={members} onChange={setMembers}
          error={errors.participants} />
      </div>

      <div className="memo-panel">
        <h3 className="memo-panel-title">
          <UserSquare size={15} aria-hidden="true" /> FRO
        </h3>
        <p className="lr-page-sub">
          First reporting officer. Optional — they can access the minute, and they are
          who <b>Submit for Draft Review</b> sends it to.
        </p>
        {fro ? (
          <div className="min-person">
            <span className="min-person-main">
              <span className="min-person-name">{fro.full_name}</span>
              {fro.designation && (
                <span className="min-person-sub">{fro.designation}</span>
              )}
            </span>
            <button type="button" className="memo-icon-btn is-danger"
              aria-label={`Remove ${fro.full_name}`} onClick={() => setFro(null)}><X size={18} aria-hidden="true" /></button>
          </div>
        ) : (
          <EmployeeSelector onSelect={setFro}
            placeholder="Select the first reporting officer (if required)…" />
        )}
      </div>

      <div className="memo-panel">
        <h3 className="memo-panel-title">Meeting Agenda/Discussion/Decisions</h3>

        <div className="min-reference-row">
          <label className="lr-field">
            <span>Reference No</span>
            <input value={lookup || form.reference_number}
              onChange={(e) => { setLookup(e.target.value);
                setForm({ ...form, reference_number: e.target.value }); }}
              placeholder="Previous meeting minute reference no" />
          </label>
          <button type="button" className="lr-btn"
            disabled={search.isPending || !(lookup || form.reference_number).trim()}
            onClick={() => { setLookup(lookup || form.reference_number);
              search.mutate(); }}>
            <Search size={14} /> {search.isPending ? 'Searching…' : 'Search'}
          </button>
        </div>
        <p className="lr-page-sub">
          Searching a previous minute's reference copies its agenda in below, to edit
          from there.
        </p>

        <RichTextEditor value={form.agenda_body}
          onChange={(html) => setForm({ ...form, agenda_body: html })}
          placeholder="S.N. · Agendas · Responsibility (Primary, Secondary) · Deadline" />
      </div>

      <div className="memo-modal-actions" style={{ marginBottom: 32 }}>
        <button type="button" className="lr-btn lr-btn-ghost"
          onClick={() => navigate(-1)}>Cancel</button>
        <button type="button" className="lr-btn" disabled={!canSave || busy}
          onClick={() => save(null)}>
          <Save size={14} /> {busy ? 'Saving…' : 'Save as draft'}
        </button>
        <button type="button" className="lr-btn" disabled={!canSave || busy || !fro}
          title={!fro ? 'Choose an FRO to send a draft review to' : undefined}
          onClick={() => save('review')}>
          <Send size={14} /> Submit for Draft Review
        </button>
        <button type="button" className="lr-btn lr-btn-primary"
          disabled={!canSave || busy || !presentCount}
          title={!presentCount
            ? 'Add at least one member present — they are who acknowledges'
            : undefined}
          onClick={() => save('acknowledge')}>
          <Send size={14} /> Submit for Acknowledge
        </button>
      </div>

      {toast && <Toast {...toast} onClose={() => setToast(null)} />}
    </div>
  );
};

export default MinuteForm;
