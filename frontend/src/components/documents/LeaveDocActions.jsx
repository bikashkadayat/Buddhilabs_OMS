import React, { useState } from 'react';
import { Eye, FileDown, Loader, X } from 'lucide-react';
import { documentService, saveBlob } from '../../services/documentService';

/**
 * Download / preview actions for an APPROVED leave's PDF documents. Shows a
 * progress indicator per action and a preview modal (iframe) for the PDF.
 * @param {{ leaveId: string }} props
 */
const LeaveDocActions = ({ leaveId }) => {
  const [busy, setBusy] = useState(null);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [error, setError] = useState(false);

  const download = async (key, fetcher, filename) => {
    setBusy(key);
    setError(false);
    try {
      const res = await fetcher(leaveId);
      saveBlob(res, filename);
    } catch {
      setError(true);
    } finally {
      setBusy(null);
    }
  };

  const preview = async () => {
    setBusy('preview');
    setError(false);
    try {
      const res = await documentService.leavePdf(leaveId);
      setPreviewUrl(URL.createObjectURL(new Blob([res.data], { type: 'application/pdf' })));
    } catch {
      setError(true);
    } finally {
      setBusy(null);
    }
  };

  const closePreview = () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl(null);
  };

  const spin = (k) => busy === k;

  return (
    <div className="lr-doc-actions">
      <button type="button" className="lr-btn lr-btn-ghost" disabled={Boolean(busy)}
        onClick={() => download('pdf', documentService.leavePdf, `leave-${leaveId}.pdf`)}>
        {spin('pdf') ? <Loader size={13} className="lr-spin" /> : <FileDown size={13} />} PDF
      </button>
      <button type="button" className="lr-btn lr-btn-ghost" disabled={Boolean(busy)}
        onClick={() => download('cert', documentService.leaveCertificate, `certificate-${leaveId}.pdf`)}>
        {spin('cert') ? <Loader size={13} className="lr-spin" /> : <FileDown size={13} />} Certificate
      </button>
      <button type="button" className="lr-btn lr-btn-ghost" disabled={Boolean(busy)} aria-label="Preview PDF" onClick={preview}>
        {spin('preview') ? <Loader size={13} className="lr-spin" /> : <Eye size={13} />}
      </button>
      {error && <span style={{ fontSize: 'var(--fs-label)', color: 'var(--danger)' }}>Failed</span>}

      {previewUrl && (
        <div className="lr-modal-overlay" role="dialog" aria-modal="true" aria-label="Leave PDF preview" onClick={closePreview}>
          <div className="lr-modal lr-pdf-modal" onClick={(e) => e.stopPropagation()}>
            <div className="lr-modal-head">
              <h3>Leave document preview</h3>
              <button type="button" className="lr-modal-close" aria-label="Close" onClick={closePreview}><X size={18} aria-hidden="true" /></button>
            </div>
            <iframe title="Leave PDF preview" src={previewUrl} className="lr-pdf-frame" />
          </div>
        </div>
      )}
    </div>
  );
};

export default LeaveDocActions;
