import React, { useCallback, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation } from '@tanstack/react-query';
import { Paperclip, Save, Send, X } from 'lucide-react';
import { memoService } from '../../services/memoService';
import MemoSectionsEditor from '../../components/memo/MemoSectionsEditor';
import ApprovalMatrixEditor from '../../components/memo/ApprovalMatrixEditor';
import { MEMO_TYPES, RESTRICTED_TYPES } from '../../components/memo/memoLabels';
import ConfirmModal from '../../components/admin/ConfirmModal';
import { useAuth } from '../../hooks/useAuth';
import { useDocumentDraft } from '../../hooks/useDocumentDraft';
import { describeMemoError } from '../../services/memoErrors';
import useExitGuard from '../../hooks/useExitGuard';
import SaveIndicator from '../../components/drafts/SaveIndicator';
import DraftRecoveryDialog from '../../components/drafts/DraftRecoveryDialog';
import VersionHistory from '../../components/drafts/VersionHistory';

/**
 * Create Memo — the manual's form, field for field (E-memo-manual pp. 3-4).
 *
 *     To*   From (Department / Department Unit / Department sub unit)
 *     CC    Date*   Subject*   Reference Memo   Memo Type*
 *     Background · Recommendation · "+ Add more"
 *
 * Date is shown and not editable: "Memo Created Date (Automatic set to current
 * date)". Department is the author's own and likewise fixed; the unit and sub-unit
 * below it are the selectable part, because they are what narrows view access.
 */
const DEFAULT_SECTIONS = [
  { title: 'Background', body: '' },
  { title: 'Recommendation', body: '' },
];

const CreateMemo = () => {
  const navigate = useNavigate();
  const { user } = useAuth();
  const fileRef = useRef(null);
  const [form, setForm] = useState({
    to_line: '', subject: '', memo_type: 'general', reference_number: '',
    department_unit: '', department_sub_unit: '',
  });
  const [ccIds, setCcIds] = useState([]);
  const [sections, setSections] = useState(DEFAULT_SECTIONS);
  const [matrixRows, setMatrixRows] = useState([]);
  const [files, setFiles] = useState([]);
  const [fileName, setFileName] = useState('');
  const [error, setError] = useState(null);
  const [matrixError, setMatrixError] = useState(null);
  const [created, setCreated] = useState(null);
  /**
   * THE MEMO THIS FORM HAS ALREADY CREATED, IF A LATER STEP FAILED.
   *
   * Creating the memo is the first of up to four writes. When a later one was
   * rejected the row stayed behind, and the next click created ANOTHER — one
   * subject in the local database has eleven identical drafts from a single
   * minute, and 29 drafts carry no attachments at all. A ref rather than state
   * because reusing it must not depend on a re-render having happened.
   */
  const pendingMemo = useRef(null);
  /**
   * A CLICK GUARD THAT IS SET SYNCHRONOUSLY.
   *
   * `busy` is derived from react-query state, which only disables the button on
   * the NEXT render — five clicks dispatched inside one event-loop turn all get
   * through, and the reuse ref above is set too late to help because none of
   * the five has reached its `await` yet. Five real memos are created; against
   * SQLite four of them additionally 500 with "database is locked" from the
   * numbering lock. A ref written before mutate() is the only thing that closes
   * the window.
   */
  const inFlight = useRef(false);
  const [discarding, setDiscarding] = useState(false);

  const { data: templates = [] } = useQuery({
    queryKey: ['memo-templates'], queryFn: memoService.listTemplates,
  });
  const { data: departments = [] } = useQuery({
    queryKey: ['memo-departments'], queryFn: memoService.listDepartments,
    staleTime: 10 * 60 * 1000,
  });

  // leaves.Department is self-nesting, so a sub-unit is a child of the chosen
  // unit. Narrowing the second dropdown to the first's children is what makes the
  // pair meaningful rather than two lists of everything.
  const units = useMemo(() => departments.filter((d) => !d.parent), [departments]);
  const subUnits = useMemo(
    () => departments.filter((d) => d.parent && String(d.parent) === String(form.department_unit)),
    [departments, form.department_unit],
  );

  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });
  const dirty = form.to_line || form.subject || files.length || matrixRows.length
    || sections.some((s) => s.body);
  const detailsValid = form.to_line.trim() && form.subject.trim();
  // MemoSection.title is a required column. A blank one rejected the whole memo
  // AFTER the memo row had been created, so the author lost the submit and
  // gained an orphan draft. Mirrored here for the same reason the matrix rule
  // is: the server stays the authority, but no click reaches a certain 400.
  const blankSection = sections.findIndex((s) => !(s.title || '').trim());
  const sectionsValid = blankSection === -1;
  // Mirrors the server's structural rule so "Submit for Approval" is never enabled
  // into a guaranteed 400. The server remains the authority.
  const matrixValid = matrixRows.length > 0
    && matrixRows[matrixRows.length - 1].role_type === 'approver'
    && !matrixRows.some((row, i) => row.role_type === 'approver' && i !== matrixRows.length - 1);

  /* ---------------------------------------------------------------- *
   * Autosave (Phase 111)
   *
   * The whole form is one snapshot. Files carry NAME AND SIZE ONLY: the
   * browser's File objects cannot outlive the process, so the dialog tells the
   * user what to re-attach rather than pretending the bytes survived.
   * ---------------------------------------------------------------- */
  const draftPayload = useMemo(() => ({
    form, ccIds, sections, matrixRows,
    attachments: files.map((f) => ({ name: f.name, size: f.size, type: f.type })),
    fileName,
  }), [form, ccIds, sections, matrixRows, files, fileName]);

  const draft = useDocumentDraft({
    kind: 'memo',
    documentKey: 'new',
    payload: draftPayload,
    userId: user?.id,
    enabled: Boolean(user?.id) && !created,
    // A confidential memo never gets a local copy — the system deliberately
    // avoids durable unauthenticated copies of restricted content.
    confidential: RESTRICTED_TYPES.includes(form.memo_type),
  });

  useExitGuard(dirty && !created && draft.status !== 'saved');

  const applySnapshot = useCallback((payload) => {
    if (!payload) return;
    if (payload.form) setForm((prev) => ({ ...prev, ...payload.form }));
    if (Array.isArray(payload.ccIds)) setCcIds(payload.ccIds);
    if (Array.isArray(payload.sections)) setSections(payload.sections);
    if (Array.isArray(payload.matrixRows)) setMatrixRows(payload.matrixRows);
    if (payload.fileName) setFileName(payload.fileName);
  }, []);


  const loadTemplate = (id) => {
    const template = templates.find((t) => String(t.id) === String(id));
    if (!template) return;
    setForm((f) => ({
      ...f,
      memo_type: template.memo_type || f.memo_type,
      subject: template.subject_template || '',
    }));
    if (template.body_template) {
      setSections((current) => current.map((s, index) => (
        index === 0 ? { ...s, body: template.body_template } : s)));
    }
  };

  const header = ({ includeMatrix }) => {
    const fields = {
      ...form,
      department_unit: form.department_unit || null,
      department_sub_unit: form.department_sub_unit || null,
      cc_department_ids: ccIds,
    };
    if (includeMatrix) {
      fields.workflow = matrixRows.map(({ assignee_id, role_type }) => ({
        assignee_id, role_type,
      }));
    }
    return fields;
  };

  /**
   * The memo row for this form: created once, then REUSED by every retry.
   *
   * A retry re-sends the header as a PATCH, so edits made while fixing whatever
   * was rejected are not lost against the row created by the first attempt.
   */
  const ensureMemo = async () => {
    const fields = header({ includeMatrix: false });
    if (pendingMemo.current) {
      return memoService.updateMemo(pendingMemo.current.id, fields)
        // A header PATCH that fails must not cost the reuse: the row still
        // exists, and creating a second one is the behaviour being fixed.
        .catch(() => pendingMemo.current);
    }
    const memo = await memoService.createMemo(fields);
    pendingMemo.current = memo;
    return memo;
  };

  /** Sections and files are saved after the memo exists, since both need its id. */
  const saveChildren = async (memoId) => {
    await memoService.setSections(memoId, sections.map((s, position) => ({
      position, title: s.title, body: s.body || '',
    })));
    if (files.length) {
      try {
        await memoService.uploadAttachments(memoId, files, { name: fileName });
      } catch (e) {
        // describeMemoError reads `files`, `detail` and bare field errors
        // alike. Reading only `.files` meant a rejection filed anywhere else —
        // a batch limit, say — fell through to the generic sentence and the
        // server's actual reason ("These 10 files total 94MB…") was discarded.
        setError(describeMemoError(e?.response?.data,
          'A file could not be attached.'));
        // RETHROWN, not swallowed. Catching it here let a failed upload finish
        // as a success: the mutation resolved, the success dialog appeared with
        // a memo number, and the error was never seen. A memo that was supposed
        // to carry a quotation and does not is not a success.
        throw e;
      }
    }
  };

  const saveDraft = useMutation({
    mutationFn: async () => {
      const memo = await ensureMemo();
      await saveChildren(memo.id);
      // The matrix is saved separately for a draft so a structural problem with
      // it never blocks capturing the memo text itself.
      if (matrixRows.length) {
        try {
          await memoService.setMatrix(memo.id, matrixRows.map(({ assignee_id, role_type }) => ({
            assignee_id, role_type,
          })));
        } catch (e) {
          const detail = e?.response?.data?.workflow;
          setMatrixError(Array.isArray(detail) ? detail[0]
            : (detail || 'The workflow could not be saved with the draft.'));
        }
      }
      return memo;
    },
    onSuccess: (memo) => {
      pendingMemo.current = null;
      // The document is real now, so the snapshot must go: otherwise the next
      // visit to Create Memo offers to restore content already committed.
      draft.complete();
      setCreated({ id: memo.id, memo_number: memo.memo_number });
    },
    onError: (e) => setError(
      describeMemoError(e?.response?.data, 'Could not save the memo.')),
  });

  const submit = useMutation({
    /**
     * CREATE AS A DRAFT, FILL IT, THEN SUBMIT — in that order.
     *
     * This used to call `create-and-submit` and save the children afterwards,
     * which is backwards: submitting locks the memo, so every following write
     * hit a locked document. Sections came back 403 twelve times in one
     * session's server log, and the attachments that followed them never ran.
     * The user saw a success dialog with a memo number and a memo with no body
     * and no files.
     *
     * The old comment justified the order as "atomic create + submit: a
     * rejected matrix leaves no orphan draft". That trade is worth reversing.
     * A rejected submit now leaves a DRAFT holding the author's sections and
     * uploads, which they can fix and resubmit; the previous behaviour
     * discarded their work to avoid leaving a row behind.
     */
    mutationFn: async () => {
      const memo = await ensureMemo();
      // Sections and files first, while the memo is still writable.
      await saveChildren(memo.id);
      if (matrixRows.length) {
        await memoService.setMatrix(memo.id, matrixRows.map(
          ({ assignee_id, role_type }) => ({ assignee_id, role_type })));
      }
      // Only now does it lock.
      await memoService.sendForReview(memo.id);
      return memo;
    },
    onSuccess: (memo) => {
      pendingMemo.current = null;
      draft.complete();
      setCreated({ id: memo.id, memo_number: memo.memo_number, submitted: true });
    },
    onError: (e) => {
      const payload = e?.response?.data || {};
      const workflow = Array.isArray(payload.workflow) ? payload.workflow[0] : payload.workflow;
      if (workflow) setMatrixError(workflow);
      else {
        setError(describeMemoError(
          payload, 'Could not submit the memo for approval.'));
      }
    },
  });

  const busy = saveDraft.isPending || submit.isPending;

  /** Fire a mutation at most once until it settles, however many clicks land. */
  const runOnce = (mutation) => () => {
    if (inFlight.current) return;
    inFlight.current = true;
    mutation.mutate(undefined, {
      onSettled: () => { inFlight.current = false; },
    });
  };

  const reset = () => {
    pendingMemo.current = null;
    setCreated(null);
    setForm({
      to_line: '', subject: '', memo_type: 'general', reference_number: '',
      department_unit: '', department_sub_unit: '',
    });
    setCcIds([]);
    setSections(DEFAULT_SECTIONS);
    setMatrixRows([]);
    setFiles([]);
    setFileName('');
    setError(null);
    setMatrixError(null);
  };

  const today = new Date().toISOString().slice(0, 10);

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

      <div className="lr-page-head">
        <div>
          <h2>Create Memo</h2>
          <div className="lr-page-sub">
            Internal Memorandum — draft it, build the approving chain, then submit
          </div>
        </div>
        <SaveIndicator status={draft.status} lastSavedAt={draft.lastSavedAt} />
      </div>

      <div className="memo-create-grid">
        <div className="memo-create-main">
          <div className="memo-panel">
            <h3 className="memo-panel-title">Internal Memorandum</h3>

            {templates.length > 0 && (
              <label className="lr-field">
                <span>Load from template (optional)</span>
                <select defaultValue="" onChange={(e) => loadTemplate(e.target.value)}
                  aria-label="Load template">
                  <option value="">— none —</option>
                  {templates.map((t) => (
                    <option key={t.id} value={t.id}>{t.name}</option>
                  ))}
                </select>
              </label>
            )}

            <label className="lr-field">
              <span>To <span aria-hidden>*</span></span>
              <input value={form.to_line} onChange={set('to_line')} aria-label="To"
                maxLength={255}
                placeholder="The approver's functional title, e.g. CEO" />
            </label>

            <div className="lr-field">
              <span>From</span>
              <div className="memo-field-row">
                <label className="lr-field">
                  <span className="memo-sub-label">Department Unit</span>
                  <select value={form.department_unit} aria-label="Department Unit"
                    onChange={(e) => setForm({
                      ...form, department_unit: e.target.value, department_sub_unit: '',
                    })}>
                    <option value="">All</option>
                    {units.map((d) => (
                      <option key={d.id} value={d.id}>{d.name}</option>
                    ))}
                  </select>
                </label>
                <label className="lr-field">
                  <span className="memo-sub-label">Department sub unit</span>
                  <select value={form.department_sub_unit} aria-label="Department sub unit"
                    disabled={!subUnits.length}
                    onChange={set('department_sub_unit')}>
                    <option value="">All</option>
                    {subUnits.map((d) => (
                      <option key={d.id} value={d.id}>{d.name}</option>
                    ))}
                  </select>
                </label>
              </div>
              <p className="lr-page-sub">
                Your own department is used automatically. Narrow it by unit or
                sub-unit to limit who can view the memo.
              </p>
            </div>

            <label className="lr-field">
              <span>CC</span>
              <select multiple value={ccIds} aria-label="CC"
                onChange={(e) => setCcIds(
                  Array.from(e.target.selectedOptions).map((o) => o.value))}>
                {departments.map((d) => (
                  <option key={d.id} value={d.id}>{d.name}</option>
                ))}
              </select>
              <p className="lr-page-sub">
                On a GENERAL memo, employees in these departments can also view it
                once archived.
              </p>
            </label>

            <div className="memo-field-row">
              <label className="lr-field">
                <span>Date</span>
                <input value={today} readOnly aria-label="Date" />
              </label>
              <label className="lr-field">
                <span>Memo Type <span aria-hidden>*</span></span>
                <select value={form.memo_type} onChange={set('memo_type')}
                  aria-label="Memo Type">
                  {MEMO_TYPES.map((type) => (
                    <option key={type.code} value={type.code}>{type.label}</option>
                  ))}
                </select>
              </label>
            </div>
            {form.memo_type === 'draft' && (
              <p className="memo-notice is-warn" role="status">
                A DRAFT memo is circulated for feedback before the memo is raised
                formally. It is temporary and <b>no log record is kept</b> for it.
              </p>
            )}
            {form.memo_type === 'confidential' && (
              <p className="memo-notice" role="status">
                A CONFIDENTIAL memo can be read only by the people who hold a role
                on it, within the department, unit or sub-unit selected above.
              </p>
            )}

            <label className="lr-field">
              <span>Subject <span aria-hidden>*</span></span>
              <input value={form.subject} onChange={set('subject')}
                aria-label="Subject" />
            </label>

            <label className="lr-field">
              <span>Reference Memo</span>
              <input value={form.reference_number} onChange={set('reference_number')}
                aria-label="Reference Memo"
                placeholder="The reference code of a related memo" />
            </label>
          </div>

          <div className="memo-panel">
            <MemoSectionsEditor sections={sections} onChange={setSections}
              invalidIndex={blankSection} />
          </div>

          <div className="memo-panel">
            <h3 className="memo-panel-title">
              <Paperclip size={15} aria-hidden="true" /> Supportive files
            </h3>
            <label className="lr-field">
              <span>File Name</span>
              <input value={fileName} onChange={(e) => setFileName(e.target.value)}
                aria-label="File Name"
                placeholder="What to call this document" />
            </label>
            <div className="memo-file-row">
              <input ref={fileRef} type="file" multiple
                accept=".pdf,.doc,.docx,.xls,.xlsx,.csv,.txt,.ppt,.pptx,.png,.jpg,.jpeg,.webp,.zip"
                aria-label="Choose file"
                onChange={(e) => setFiles(Array.from(e.target.files || []))} />
            </div>
            <p className="lr-page-sub">
              Accepts only PDF, doc, docx, xls, xlsx, csv. A single file larger
              than 10 MB will not be attached.
            </p>
            {files.length > 0 && (
              <ul className="memo-file-list">
                {files.map((file) => (
                  <li key={file.name}>
                    <span>{file.name}</span>
                    <button type="button" className="memo-icon-btn"
                      aria-label={`Remove ${file.name}`}
                      onClick={() => setFiles(files.filter((f) => f !== file))}>
                      <X size={14} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        <aside className="memo-create-side">
          <div className="memo-panel">
            <ApprovalMatrixEditor rows={matrixRows} onChange={setMatrixRows}
              error={matrixError} />
          </div>

          {error && <p className="memo-form-error" role="alert">{error}</p>}

          <div className="memo-modal-actions">
            <button type="button" className="lr-btn lr-btn-ghost"
              onClick={() => (dirty ? setDiscarding(true) : navigate('/memos'))}>
              Cancel
            </button>
            <button type="button" className="lr-btn"
              disabled={!detailsValid || !sectionsValid || busy}
              title={!sectionsValid
                ? `Give section ${blankSection + 1} a title` : undefined}
              onClick={runOnce(saveDraft)}>
              <Save size={14} /> {busy ? 'Saving…' : 'Save as draft'}
            </button>
            <button type="button" className="lr-btn lr-btn-primary"
              disabled={!detailsValid || !sectionsValid || !matrixValid || busy}
              title={(!sectionsValid && `Give section ${blankSection + 1} a title`)
                || (!matrixValid
                  && 'Build the approving chain — the last step must be an Approver')
                || undefined}
              onClick={runOnce(submit)}>
              <Send size={14} /> Submit for Approval
            </button>
          </div>
        </aside>
      </div>

      {created && (
        <div className="lr-modal-overlay" role="dialog" aria-modal="true"
          aria-label="Memo created">
          <div className="lr-modal">
            <div className="lr-modal-head"><h3>{created.memo_number}</h3></div>
            <p style={{ fontSize: 'var(--fs-body)', color: 'var(--text-secondary)' }}>
              {created.submitted
                ? 'The memo has been submitted and assigned to the first person in the approving chain.'
                : 'Saved as a draft. You can submit it for approval from the memo itself.'}
            </p>
            <div className="memo-modal-actions">
              <button type="button" className="lr-btn lr-btn-ghost" onClick={reset}>
                Create another
              </button>
              <button type="button" className="lr-btn lr-btn-primary"
                onClick={() => navigate(`/memos/${created.id}`)}>
                Open the memo
              </button>
            </div>
          </div>
        </div>
      )}

      {discarding && (
        <ConfirmModal
          title="Discard this memo?"
          message="Nothing you have typed will be kept."
          danger confirmLabel="Discard"
          onClose={() => setDiscarding(false)}
          onConfirm={() => navigate('/memos')} />
      )}
    </div>
  );
};

export default CreateMemo;
