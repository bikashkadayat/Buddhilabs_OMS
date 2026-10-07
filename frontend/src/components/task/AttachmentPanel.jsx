import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Download, ExternalLink, Eye, Link2, Paperclip, Tag, Trash2, Upload,
} from 'lucide-react';

import { taskService } from '../../services/taskService';
import { saveBlob } from '../../services/reportService';
import { fmtDate, fmtDateTime } from './taskLabels';

/**
 * Attachments and evidence (Phase T2.4 / T2.5).
 *
 * ONE COMPONENT, TWO SECTIONS
 * ---------------------------
 * Evidence and general attachments are the same row with a different flag, so
 * they are the same component with a different `variant`. The detail page
 * renders it twice. Two components would have meant two upload flows, two
 * removal confirmations and two places for the flag toggle to drift.
 *
 * The distinction is worth keeping in the UI even though it is one boolean: a
 * reviewer opening the task wants what was PRODUCED, not the brief it was
 * produced against.
 */

const humanSize = (bytes) => {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

/** The download log, fetched only when somebody asks for it. */
const DownloadLog = ({ taskId, attachmentId }) => {
  const { data: rows, isLoading, isError } = useQuery({
    queryKey: ['tasks', taskId, 'downloads', attachmentId],
    queryFn: () => taskService.getDownloadLog(taskId, attachmentId),
    retry: false,
  });

  if (isLoading) return <p className="task-sub">Loading…</p>;
  if (isError) return <p className="task-sub">The download log is unavailable.</p>;
  if (!rows?.length) return <p className="task-sub">Nobody has opened this yet.</p>;

  return (
    <ul className="task-download-log">
      {rows.map((row) => (
        <li key={row.id}>
          <span>{row.user_name || 'Unknown'}</span>
          <span className="task-sub">{fmtDateTime(row.downloaded_at)}</span>
        </li>
      ))}
    </ul>
  );
};

const Row = ({ file, taskId, canManage, canViewLog, busy, onFlag, onRemove }) => {
  const [showLog, setShowLog] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState('');

  /**
   * FETCH the file, do not link to it.
   *
   * `file.download_url` points at an authenticated, task-scoped view — tasks
   * deliberately do not use the signed media URLs that memo, minute and
   * circular attachments use, because per-request authorisation is what makes
   * the download log below possible. An `<a href>` navigates without the
   * Authorization header, so it downloaded a 401 body: "Authentication
   * credentials were not provided."
   */
  const download = async () => {
    setDownloading(true);
    setDownloadError('');
    try {
      const res = await taskService.downloadAttachment(taskId, file.id);
      saveBlob(res, file.original_name || 'attachment');
    } catch {
      // Stated, not swallowed: a download that silently does nothing is the
      // same experience as the bug this replaced.
      setDownloadError('That file could not be downloaded. Try again.');
    } finally {
      setDownloading(false);
    }
  };
  const isLink = file.kind === 'link';

  return (
    <li className="task-file">
      <span className="task-file-main">
        {isLink ? <Link2 size={13} aria-hidden="true" />
          : <Paperclip size={13} aria-hidden="true" />}
        {isLink ? (
          // rel="noreferrer" as well as noopener: an external evidence link must
          // not leak the task URL it was reached from in the Referer header.
          <a href={file.link_url} target="_blank" rel="noopener noreferrer">
            {file.original_name || file.link_url}
            <ExternalLink size={11} aria-hidden="true" />
          </a>
        ) : (
          <button type="button" className="task-file-name" onClick={download}
            disabled={downloading || busy}>
            {file.original_name}
          </button>
        )}
      </span>

      <span className="task-sub">
        {file.uploaded_by_name} · {fmtDate(file.uploaded_at)}
        {!isLink && file.size ? ` · ${humanSize(file.size)}` : ''}
        {!isLink && file.download_count > 0
          ? ` · ${file.download_count} download${file.download_count === 1 ? '' : 's'}`
          : ''}
      </span>

      {downloadError && (
        <span className="task-sub is-error" role="alert">{downloadError}</span>
      )}

      {file.caption && <span className="task-sub">{file.caption}</span>}

      <span className="task-file-actions">
        {canManage && (
          <>
            <button type="button" className="task-link-btn" disabled={busy}
              onClick={() => onFlag(file.id, !file.is_evidence)}>
              <Tag size={12} />
              {file.is_evidence ? 'Mark as reference' : 'Mark as evidence'}
            </button>
            <button type="button" className="task-link-btn is-danger" disabled={busy}
              onClick={() => onRemove(file.id)}>
              <Trash2 size={12} /> Remove
            </button>
          </>
        )}
        {canViewLog && !isLink && (
          <button type="button" className="task-link-btn"
            aria-expanded={showLog} onClick={() => setShowLog(!showLog)}>
            <Eye size={12} /> Who opened it
          </button>
        )}
      </span>

      {showLog && <DownloadLog taskId={taskId} attachmentId={file.id} />}
    </li>
  );
};

const AttachmentPanel = ({
  taskId, files = [], removed = [], variant = 'evidence',
  canUpload, canViewLog, currentUserId, isOwner, busy,
  onUpload, onAddLink, onFlag, onRemove,
}) => {
  const [linking, setLinking] = useState(false);
  const [linkUrl, setLinkUrl] = useState('');
  const [caption, setCaption] = useState('');
  const [showRemoved, setShowRemoved] = useState(false);
  const isEvidence = variant === 'evidence';

  // Mirrors tasks.permissions.can_remove_attachment / can_flag_evidence: the
  // uploader or the task's owner. `uploaded_by` is the uploader's id, which the
  // serializer sends for exactly this decision. The server enforces the rule;
  // this only avoids offering a button that would 403.
  const mayManage = (file) => Boolean(
    isOwner || (currentUserId && file.uploaded_by === currentUserId),
  );

  return (
    <>
      {files.length === 0 && (
        <p className="task-sub">
          {isEvidence ? 'No evidence uploaded yet.' : 'No reference files.'}
        </p>
      )}
      <ul className="task-files">
        {files.map((file) => (
          <Row key={file.id} file={file} taskId={taskId}
            canManage={canUpload && mayManage(file)}
            canViewLog={canViewLog} busy={busy}
            onFlag={onFlag} onRemove={onRemove} />
        ))}
      </ul>

      {canUpload && (
        <div className="task-form-actions" style={{ justifyContent: 'flex-start' }}>
          <label className="lr-btn task-upload">
            <Upload size={14} /> {isEvidence ? 'Upload evidence' : 'Upload file'}
            <input type="file" multiple className="sr-only"
              onChange={(e) => {
                if (!e.target.files?.length) return;
                // Copy the FileList before resetting the input: clearing `value`
                // empties `files`, so a later read would upload nothing.
                const chosen = Array.from(e.target.files);
                e.target.value = '';
                onUpload(chosen, isEvidence);
              }} />
          </label>
          {isEvidence && (
            <button type="button" className="lr-btn" onClick={() => setLinking(!linking)}>
              <Link2 size={14} /> Add a link
            </button>
          )}
        </div>
      )}

      {linking && (
        <div className="task-form-row">
          <label className="lr-field" style={{ flex: 2 }}>
            <span className="sr-only">Link URL</span>
            <input value={linkUrl} placeholder="https://…" aria-label="Link URL"
              onChange={(e) => setLinkUrl(e.target.value)} />
          </label>
          <label className="lr-field" style={{ flex: 1 }}>
            <span className="sr-only">Link caption</span>
            <input value={caption} placeholder="What is it?" aria-label="Link caption"
              onChange={(e) => setCaption(e.target.value)} />
          </label>
          <button type="button" className="lr-btn lr-btn-primary"
            disabled={busy || !linkUrl.trim()}
            onClick={() => {
              onAddLink(linkUrl.trim(), caption.trim());
              setLinkUrl('');
              setCaption('');
              setLinking(false);
            }}>
            Add
          </button>
        </div>
      )}

      {isEvidence && removed.length > 0 && (
        <div className="task-removed">
          <button type="button" className="task-link-btn" aria-expanded={showRemoved}
            onClick={() => setShowRemoved(!showRemoved)}>
            <Download size={12} /> Withdrawal history ({removed.length})
          </button>
          {showRemoved && (
            <ul className="task-files">
              {removed.map((file) => (
                <li key={file.id} className="task-file is-removed">
                  <span className="task-file-main">
                    <s>{file.original_name || file.link_url}</s>
                  </span>
                  <span className="task-sub">
                    removed by {file.removed_by_name} · {fmtDate(file.removed_at)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </>
  );
};

export default AttachmentPanel;
