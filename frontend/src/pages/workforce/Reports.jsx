import React, { useState } from 'react';
import { Download, FileSpreadsheet, FileText, Table } from 'lucide-react';
import { reportService, saveBlob, WORKFORCE_REPORTS } from '../../services/reportService';
import DateRangePicker from '../../components/workforce/DateRangePicker';

const today = () => new Date().toISOString().slice(0, 10);
const monthStart = () => `${new Date().toISOString().slice(0, 7)}-01`;

const FORMAT_META = {
  pdf: { label: 'PDF', icon: <FileText size={14} /> },
  excel: { label: 'Excel', icon: <FileSpreadsheet size={14} /> },
  csv: { label: 'CSV', icon: <Table size={14} /> },
};

/** Poll a queued report until it is ready, then hand back the blob response. */
const POLL_MS = 900;
const POLL_LIMIT = 40;   // ~36s, well past the slowest measured build

const ReportCard = ({ report, range, onError }) => {
  const [busy, setBusy] = useState('');

  const run = async (format) => {
    setBusy(format);
    onError('');
    try {
      const run_ = await reportService.requestReport(report.key, {
        from: range.from, to: range.to, format,
      });

      let status = run_.status;
      for (let i = 0; i < POLL_LIMIT && status !== 'ready'; i += 1) {
        if (status === 'failed') throw new Error('The report could not be generated.');
        // Sequential by nature: each poll must observe the previous result.
        await new Promise((r) => setTimeout(r, POLL_MS));
        ({ status } = await reportService.getStatus(run_.id));
      }
      if (status !== 'ready') throw new Error('The report is taking longer than expected.');

      const response = await reportService.download(run_.id);
      saveBlob(response, `${report.key}.${format === 'excel' ? 'xlsx' : format}`);
    } catch (err) {
      onError(err?.response?.data?.detail || err.message || 'Could not build that report.');
    } finally {
      setBusy('');
    }
  };

  return (
    <article className="wf-report-card">
      <h3>{report.name}</h3>
      <p className="wf-muted wf-small">{report.desc}</p>
      <div className="wf-report-actions">
        {report.formats.map((format) => (
          <button
            key={format} type="button" className="wf-btn wf-btn-ghost wf-btn-sm"
            disabled={Boolean(busy)} onClick={() => run(format)}
          >
            {busy === format
              ? 'Building…'
              : <>{FORMAT_META[format].icon} {FORMAT_META[format].label}</>}
          </button>
        ))}
      </div>
    </article>
  );
};

const Reports = () => {
  const [range, setRange] = useState({ from: monthStart(), to: today() });
  const [error, setError] = useState('');

  return (
    <div className="wf-page">
      <header className="wf-page-head">
        <div>
          <h1 className="wf-title">Workforce reports</h1>
          <p className="wf-sub">
            Generated server-side and downloaded when ready. Department heads
            receive their own department; HR receives the organisation.
          </p>
        </div>
        <DateRangePicker from={range.from} to={range.to} onChange={setRange} />
      </header>

      {error && <p className="wf-inline-error" role="alert">{error}</p>}

      <section className="wf-card">
        <div className="wf-card-head">
          <h2><Download size={18} /> Available reports</h2>
        </div>
        <div className="wf-report-grid">
          {WORKFORCE_REPORTS.map((report) => (
            <ReportCard key={report.key} report={report} range={range} onError={setError} />
          ))}
        </div>
      </section>
    </div>
  );
};

export default Reports;
