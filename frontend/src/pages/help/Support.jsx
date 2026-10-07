import React, { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { ArrowLeft, MessageCircle, Bug, Lightbulb, CheckCircle2 } from 'lucide-react';

import { supportService } from '../../services/supportService';
import { describeApiError } from '../../services/apiErrors';

/**
 * Contact support, report a problem, request a feature -- without leaving
 * the workspace. The page you came from travels with the message.
 */
const KINDS = [
  { key: 'contact', label: 'Ask a question', icon: MessageCircle, hint: 'How do I…? Where is…?' },
  { key: 'problem', label: 'Report a problem', icon: Bug, hint: 'Something isn’t working as it should.' },
  { key: 'feature', label: 'Request a feature', icon: Lightbulb, hint: 'Something that would make your work easier.' },
];
const when = (iso) => new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });

const Support = () => {
  const [params] = useSearchParams();
  const [kind, setKind] = useState(params.get('kind') || 'contact');
  const [form, setForm] = useState({ subject: params.get('subject') || '', message: '' });
  const [state, setState] = useState({ busy: false, error: '', sent: '' });
  const [mine, setMine] = useState(null);

  const load = () => supportService.mine().then(setMine).catch(() => setMine([]));
  useEffect(() => { load(); }, []);

  const submit = async (event) => {
    event.preventDefault();
    setState({ busy: true, error: '', sent: '' });
    try {
      const data = await supportService.send({ kind, ...form, page: params.get('from') || document.referrer || '' });
      setState({ busy: false, error: '', sent: data.detail });
      setForm({ subject: '', message: '' });
      load();
    } catch (e) {
      setState({ busy: false, sent: '', error: e?.response?.data?.message || describeApiError(e, 'We couldn’t send that. Please try again.') });
    }
  };

  return (
    <div className="page hc">
      <Link className="hc-back" to="/help"><ArrowLeft size={15} aria-hidden="true" /> Help</Link>
      <h1 className="hc-h1">Contact support</h1>
      <p className="hc-muted">We read every message. You’ll hear back by email.</p>

      <div className="sp-kinds" role="radiogroup" aria-label="What is it about?">
        {KINDS.map(({ key, label, icon: Icon, hint }) => (
          <button key={key} type="button" role="radio" aria-checked={kind === key}
                  className={`sp-kind${kind === key ? ' is-on' : ''}`} onClick={() => setKind(key)}>
            <Icon size={18} aria-hidden="true" />
            <strong>{label}</strong>
            <small>{hint}</small>
          </button>
        ))}
      </div>

      {state.sent && <p className="sp-ok" role="status"><CheckCircle2 size={16} aria-hidden="true" /> {state.sent}</p>}
      <form className="sp-form" onSubmit={submit}>
        <label className="acc-field">
          <span>Subject</span>
          <input value={form.subject} maxLength={200} onChange={(e) => setForm({ ...form, subject: e.target.value })}
                 placeholder={kind === 'problem' ? 'e.g. The Apply button stays grey' : 'A few words'} />
        </label>
        <label className="acc-field">
          <span>{kind === 'problem' ? 'What happened, and what did you expect?' : 'Your message'}</span>
          <textarea rows={5} required value={form.message}
                    onChange={(e) => setForm({ ...form, message: e.target.value })} />
        </label>
        {state.error && <p className="acc-err" role="alert">{state.error}</p>}
        <button type="submit" className="btn btn-primary" disabled={state.busy || !form.message.trim()}>
          {state.busy ? 'Sending…' : 'Send'}
        </button>
      </form>

      <section className="sp-mine" aria-labelledby="sp-mine-h">
        <h2 id="sp-mine-h">Your requests</h2>
        {mine === null ? <p className="hc-muted">Loading…</p> : mine.length === 0 ? (
          <p className="hc-muted">You haven’t sent us anything yet.</p>
        ) : (
          <ul className="sp-list">
            {mine.map((r) => (
              <li key={r.id}>
                <div>
                  <strong>{r.subject || r.message.slice(0, 60)}</strong>
                  <span className="hc-muted">{r.kind_display} · {when(r.created_at)}</span>
                  {r.response && <p className="sp-reply">Our reply: {r.response}</p>}
                </div>
                <span className={`pf-chip ${r.status === 'resolved' ? 'pf-chip-ok' : 'pf-chip-wait'}`}>{r.status_display}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
};

export default Support;
