import React, { useEffect, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, Download, Mail } from 'lucide-react';
import { reportService, reportTypeByKey, saveBlob } from '../../services/reportService';

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const AUDIT_ACTIONS = ['', 'create', 'update', 'delete', 'approve', 'reject', 'submit', 'login', 'other'];

const ReportBuilder = () => {
  const { type: typeKey } = useParams();
  const navigate = useNavigate();
  const type = reportTypeByKey(typeKey);
  const timer = useRef(null);

  const thisYear = new Date().getFullYear();
  const [params, setParams] = useState({ year: thisYear, format: type?.formats[0] });
  const [run, setRun] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => () => clearTimeout(timer.current), []);

  if (!type) return <div className="page"><p>Unknown report type.</p></div>;

  const set = (k) => (e) => setParams((p) => ({ ...p, [k]: e.target.value }));

  const poll = (id) => {
    const tick = async () => {
      try {
        const s = await reportService.getStatus(id);
        setRun((prev) => ({ ...prev, ...s }));
        if (s.status === 'ready' || s.status === 'failed') { setBusy(false); return; }
        timer.current = setTimeout(tick, 1500);
      } catch {
        setBusy(false);
        setError('Lost connection while generating.');
      }
    };
    timer.current = setTimeout(tick, 1200);
  };

  const generate = async () => {
    setError(null);
    setBusy(true);
    try {
      const r = await reportService.requestReport(type.key, params);
      setRun(r);
      if (r.status === 'ready' || r.status === 'failed') setBusy(false);
      else poll(r.id);
    } catch (e) {
      setBusy(false);
      setError(e?.response?.data?.detail || 'Could not start generation.');
    }
  };

  const doDownload = async () => {
    const res = await reportService.download(run.id);
    saveBlob(res, `${type.key}.${params.format === 'pdf' ? 'pdf' : 'xlsx'}`);
  };

  const hasField = (f) => type.fields.includes(f);

  return (
    <div className="page" style={{ paddingBottom: 80 }}>
      <button type="button" className="lr-btn lr-btn-ghost" onClick={() => navigate('/reports')} style={{ marginBottom: 16 }}>
        <ArrowLeft size={14} /> Back to reports
      </button>

      <div className="lr-page-head">
        <div><h2>{type.name}</h2><div className="lr-page-sub">{type.desc}</div></div>
      </div>

      <div className="lr-chart-card" style={{ maxWidth: 560 }}>
        {hasField('year') && (
          <label className="lr-field"><span>Year</span>
            <select value={params.year} onChange={set('year')} aria-label="Year">
              {[0, 1, 2, 3].map((i) => <option key={i} value={thisYear - i}>{thisYear - i}</option>)}
            </select>
          </label>
        )}
        {hasField('month') && (
          <label className="lr-field"><span>Month (optional)</span>
            <select value={params.month || ''} onChange={set('month')} aria-label="Month">
              <option value="">All months</option>
              {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
            </select>
          </label>
        )}
        {hasField('department') && (
          <label className="lr-field"><span>Department code (optional)</span>
            <input type="text" value={params.department || ''} onChange={(e) => setParams((p) => ({ ...p, department: e.target.value.toUpperCase() }))} aria-label="Department code" />
          </label>
        )}
        {hasField('date_from') && (
          <label className="lr-field"><span>From date</span><input type="date" value={params.date_from || ''} onChange={set('date_from')} aria-label="From date" /></label>
        )}
        {hasField('date_to') && (
          <label className="lr-field"><span>To date</span><input type="date" value={params.date_to || ''} onChange={set('date_to')} aria-label="To date" /></label>
        )}
        {hasField('action') && (
          <label className="lr-field"><span>Action (optional)</span>
            <select value={params.action || ''} onChange={set('action')} aria-label="Action">
              {AUDIT_ACTIONS.map((a) => <option key={a} value={a}>{a || 'All actions'}</option>)}
            </select>
          </label>
        )}
        {type.formats.length > 1 && (
          <label className="lr-field"><span>Format</span>
            <select value={params.format} onChange={set('format')} aria-label="Format">
              {type.formats.map((f) => <option key={f} value={f}>{f.toUpperCase()}</option>)}
            </select>
          </label>
        )}

        <div style={{ display: 'flex', gap: 10, marginTop: 12, alignItems: 'center' }}>
          <button type="button" className="lr-btn lr-btn-primary" onClick={generate} disabled={busy}>
            {busy ? 'Generating…' : 'Generate report'}
          </button>
          {run?.status === 'ready' && (
            <button type="button" className="lr-btn" onClick={doDownload}><Download size={14} /> Download</button>
          )}
        </div>

        {busy && <p className="lr-page-sub" style={{ marginTop: 12 }} role="status">Working — this can take a few seconds for large ranges…</p>}
        {run?.status === 'ready' && (
          <p className="lr-page-sub" style={{ marginTop: 12, color: 'var(--success)' }}>
            Report ready. <span style={{ color: 'var(--text-secondary)' }}><Mail size={12} /> Tip: schedule recurring email delivery from the admin panel.</span>
          </p>
        )}
        {run?.status === 'failed' && <p className="lr-error-msg" role="alert" style={{ color: 'var(--danger)' }}>Generation failed: {run.error}</p>}
        {error && <p className="lr-error-msg" role="alert" style={{ color: 'var(--danger)' }}>{error}</p>}
      </div>
    </div>
  );
};

export default ReportBuilder;
