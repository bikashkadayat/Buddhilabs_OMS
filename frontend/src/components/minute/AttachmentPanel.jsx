import React, { useRef, useState } from 'react';
import {
  Download, Eye, FileText, History, Paperclip, Trash2, Upload,
} from 'lucide-react';

const fmt = (iso) => (iso ? new Date(iso).toLocaleString(undefined, {
  year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
}) : '—');

// Mirrors minutes.services.MINUTE_ATTACHMENT_EXTENSIONS. Used for the file dialog's
// filter and for a friendly message BEFORE a doomed upload leaves the browser — the
// server re-checks all of it, including the file's actual leading bytes, which is the
// check that matters and the one a client cannot do.
const ALLOWED = ['pdf', 'docx', 'xlsx', 'pptx', 'png', 'jpg', 'jpeg', 'gif', 'webp'];
const ACCEPT = ALLOWED.map((ext) => `.${ext}`).join(',');
const MAX_BYTES = 25 * 1024 * 1024;

const extensionOf = (name) => (name.includes('.')
  ? name.split('.').pop().toLowerCase()
  : '');

/**
 * The attachment register (Phase 42 item 1): upload, download, preview, version history
 * and removal.
 *
 * URLS COME FROM THE SERVER, signed and expiring. This component never builds a media
 * path — Phase 31 did, pointing at /media/, a route this project deliberately does not
 * serve, so every link was broken. Preview is offered only where `is_previewable` says a
 * browser can actually render it, so "Preview" is never a button that quietly downloads
 * a .docx instead.
 *
 * @param {{ attachments:Array, canEdit:boolean, busy:boolean,
 *           onUpload:(files:FileList, opts?:{replaces?:string})=>void,
 *           onDelete:(id:string)=>void, error?:string }} props
 */
const AttachmentPanel = ({
  attachments = [], canEdit = false, busy = false, onUpload, onDelete, error,
}) => {
  const inputRef = useRef(null);
  const replaceRef = useRef(null);
  const [replacing, setReplacing] = useState(null);
  const [showHistory, setShowHistory] = useState({});
  const [localError, setLocalError] = useState(null);

  const check = (files) => {
    for (const file of Array.from(files)) {
      const ext = extensionOf(file.name);
      if (!ALLOWED.includes(ext)) {
        return `${file.name}: ".${ext}" is not an allowed file type. Allowed: ${ALLOWED.join(', ')}.`;
      }
      if (file.size > MAX_BYTES) {
        return `${file.name} is larger than 25MB.`;
      }
    }
    return null;
  };

  const pick = (event, opts = {}) => {
    const files = event.target.files;
    if (!files?.length) return;
    const problem = check(files);
    setLocalError(problem);
    if (!problem) onUpload(files, opts);
    // Reset the input so choosing the same file twice still fires a change event.
    event.target.value = '';
    setReplacing(null);
  };

  const toggleHistory = (id) =>
    setShowHistory((current) => ({ ...current, [id]: !current[id] }));

  return (
    <div className="memo-panel">
      <h3 className="memo-panel-title">
        <Paperclip size={15} aria-hidden="true" /> Attachments
        <span className="min-count">{attachments.length}</span>
      </h3>

      {(localError || error) && (
        <p className="memo-notice is-warn" role="alert">{localError || error}</p>
      )}

      {attachments.length === 0 && (
        <p className="lr-page-sub">No documents are attached to this minute.</p>
      )}

      {attachments.length > 0 && (
        <ul className="min-file-list">
          {attachments.map((file) => (
            <li key={file.id} className="min-file">
              <span className="min-file-ico" aria-hidden="true">
                <FileText size={18} />
              </span>
              <span className="min-file-main">
                <span className="min-file-name">{file.original_name}</span>
                <span className="min-file-meta">
                  {file.extension?.toUpperCase()} · {file.size_label}
                  {file.version > 1 && (
                    <span className="min-file-version">v{file.version}</span>
                  )}
                  {' · '}
                  {file.uploaded_by?.full_name || '—'} · {fmt(file.uploaded_at)}
                </span>
              </span>

              <span className="min-file-actions">
                {/* Preview only where the browser can render it. */}
                {file.preview_url && (
                  <a className="lr-btn lr-btn-ghost" href={file.preview_url}
                    target="_blank" rel="noreferrer"
                    aria-label={`Preview ${file.original_name}`}>
                    <Eye size={14} /> Preview
                  </a>
                )}
                <a className="lr-btn lr-btn-ghost" href={file.download_url}
                  aria-label={`Download ${file.original_name}`}>
                  <Download size={14} /> Download
                </a>
                {file.versions?.length > 0 && (
                  <button type="button" className="lr-btn lr-btn-ghost"
                    aria-expanded={Boolean(showHistory[file.id])}
                    onClick={() => toggleHistory(file.id)}>
                    <History size={14} /> {file.versions.length} earlier
                  </button>
                )}
                {canEdit && (
                  <>
                    <button type="button" className="lr-btn lr-btn-ghost" disabled={busy}
                      onClick={() => { setReplacing(file.id); replaceRef.current?.click(); }}>
                      <Upload size={14} /> New version
                    </button>
                    <button type="button" className="lr-btn lr-btn-ghost" disabled={busy}
                      aria-label={`Remove ${file.original_name}`}
                      onClick={() => onDelete(file.id)}>
                      <Trash2 size={14} />
                    </button>
                  </>
                )}
              </span>

              {showHistory[file.id] && file.versions?.length > 0 && (
                <ul className="min-file-history">
                  {file.versions.map((old) => (
                    <li key={old.id}>
                      <span className="min-file-version">v{old.version}</span>
                      {' '}{old.size_label} · {old.uploaded_by} · {fmt(old.uploaded_at)}
                      {' '}
                      <a href={old.download_url}
                        aria-label={`Download version ${old.version} of ${file.original_name}`}>
                        Download
                      </a>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      )}

      {canEdit && (
        <div className="min-file-upload">
          <input ref={inputRef} type="file" multiple accept={ACCEPT}
            aria-label="Attach files" style={{ display: 'none' }}
            onChange={(event) => pick(event)} />
          {/* A second, single-file input for replacements, so "new version" cannot
              accidentally take several files — the server refuses that anyway. */}
          <input ref={replaceRef} type="file" accept={ACCEPT}
            aria-label="Upload a new version" style={{ display: 'none' }}
            onChange={(event) => pick(event, { replaces: replacing })} />
          <button type="button" className="lr-btn" disabled={busy}
            onClick={() => inputRef.current?.click()}>
            <Upload size={14} /> {busy ? 'Uploading…' : 'Attach files'}
          </button>
          <span className="lr-page-sub">
            PDF, Word, Excel, PowerPoint or images · up to 25MB each · re-uploading the
            same filename adds a new version
          </span>
        </div>
      )}
    </div>
  );
};

export default AttachmentPanel;
