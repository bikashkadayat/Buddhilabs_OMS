import React from 'react';
import { Download, Eye, Paperclip } from 'lucide-react';

/**
 * Memo attachments (Phase MEMO-ATTACHMENT-ENTERPRISE).
 *
 * The backend already served everything a reader needs — size, uploader,
 * upload date, and a short-lived signed URL per file. The detail page threw all
 * of it away and rendered a bare `<ul>` of links titled "Attachment", so a
 * reviewer deciding on a procurement memo could not tell a 40KB screenshot from
 * a 2MB quotation, or who had put it there.
 *
 * WHY LINKS AND NOT FETCHES, unlike the task panel. Memo attachment URLs are
 * SIGNED and expiring — the URL carries its own credential, so an `<a href>`
 * works and the browser handles the transfer. Task attachments are served by an
 * authenticated view and must be fetched with the token instead. Both are
 * correct for their module; the difference is why `downloads.guard.test.js`
 * scopes its rule to the task module rather than banning the shape outright.
 *
 * Preview and download are separate URLs rather than one link doing both: they
 * differ only in Content-Disposition, and a reviewer reading a quotation should
 * not have to save it first.
 */
const fmtDate = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString();
};

const humanSize = (bytes) => {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

const MemoAttachmentPanel = ({ files = [], legacyUrl = '' }) => {
  const count = files.length + (legacyUrl ? 1 : 0);
  if (!count) return null;

  return (
    <section className="memo-attachments" aria-labelledby="memo-att-h">
      <h4 id="memo-att-h">
        <Paperclip size={13} aria-hidden="true" />
        {' '}Attachments <span className="memo-att-count">{count}</span>
      </h4>

      <ul className="memo-att-list">
        {/* The single-file field predates MemoAttachment. Older memos still
            carry one, and dropping it here would hide their only document. */}
        {legacyUrl && (
          <li className="memo-att">
            <span className="memo-att-main">
              <span className="memo-att-name">Attachment</span>
              <span className="memo-att-meta">Attached before this memo moved to multiple files</span>
            </span>
            <span className="memo-att-actions">
              <a className="memo-att-btn" href={legacyUrl} target="_blank" rel="noreferrer">
                <Eye size={13} aria-hidden="true" /> Open
              </a>
            </span>
          </li>
        )}

        {files.map((file) => {
          const name = file.label || file.display_name || file.original_name || 'Attachment';
          const meta = [
            humanSize(file.size),
            file.uploaded_at ? fmtDate(file.uploaded_at) : '',
            file.uploaded_by?.full_name || file.uploaded_by?.username || '',
          ].filter(Boolean).join(' · ');
          return (
            <li key={file.id} className="memo-att">
              <span className="memo-att-main">
                <span className="memo-att-name" title={file.original_name || name}>{name}</span>
                {meta && <span className="memo-att-meta">{meta}</span>}
              </span>
              <span className="memo-att-actions">
                {file.preview_url && (
                  <a className="memo-att-btn" href={file.preview_url}
                     target="_blank" rel="noreferrer">
                    <Eye size={13} aria-hidden="true" /> Preview
                  </a>
                )}
                {file.url && (
                  <a className="memo-att-btn" href={file.url}>
                    <Download size={13} aria-hidden="true" /> Download
                  </a>
                )}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
};

export default MemoAttachmentPanel;
