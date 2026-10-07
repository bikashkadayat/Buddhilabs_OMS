import React, { useState } from 'react';
import { Check, Download, Loader2 } from 'lucide-react';
import { useAnalyticsExport } from '../../hooks/useAnalytics';
import { ANALYTICS_EXPORTS } from '../../services/analyticsService';

/**
 * Queue an analytics export and hand back a download link.
 *
 * Goes through the reports hub, so the file inherits async generation, a signed
 * expiring URL, audit logging and the retention purge. Scope is injected
 * server-side — nothing about the requester's department is sent from here,
 * which is why editing this component cannot widen an export.
 */
const ExportMenu = ({ params = {}, orgAnalyst = false, only }) => {
  const [open, setOpen] = useState(false);
  const [ready, setReady] = useState(null);
  const exporter = useAnalyticsExport();

  const options = ANALYTICS_EXPORTS
    .filter((option) => (option.orgOnly ? orgAnalyst : true))
    .filter((option) => (only ? only.includes(option.type) : true));

  const run = (option) => {
    setReady(null);
    exporter.mutate(
      { reportType: option.type, format: option.format, params },
      {
        onSuccess: (status) => {
          setReady({ ...status, label: option.label });
          // Opening in a new tab rather than navigating: the dashboard the user
          // exported from should still be there when the download finishes.
          if (status.file_url) window.open(status.file_url, '_blank', 'noopener');
        },
      },
    );
  };

  return (
    <div className="an-export">
      <button
        type="button" className="wf-btn wf-btn-ghost"
        aria-expanded={open} aria-haspopup="menu"
        onClick={() => setOpen((value) => !value)}
      >
        <Download size={14} aria-hidden="true" /> Export
      </button>

      {open && (
        <ul className="an-export-menu" role="menu">
          {options.map((option) => (
            <li key={option.type} role="none">
              <button
                type="button" role="menuitem" className="an-export-item"
                disabled={exporter.isPending}
                onClick={() => run(option)}
              >
                {option.label}
              </button>
            </li>
          ))}
        </ul>
      )}

      {exporter.isPending && (
        <span className="an-export-state" role="status">
          <Loader2 size={13} className="an-spin" aria-hidden="true" /> Generating…
        </span>
      )}
      {exporter.isError && (
        <span className="an-export-state an-tone-bad" role="alert">
          {exporter.error?.message || 'The export failed.'}
        </span>
      )}
      {ready && !exporter.isPending && (
        <span className="an-export-state an-tone-ok" role="status">
          <Check size={13} aria-hidden="true" />
          {ready.file_url
            ? <a href={ready.file_url} target="_blank" rel="noopener noreferrer">
                Download {ready.label}
              </a>
            : `${ready.label} is ready in Reports → History`}
        </span>
      )}
    </div>
  );
};

export default ExportMenu;
