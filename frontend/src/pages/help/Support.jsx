import React, { useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import {
  Bug, Wrench, Clock3, CalendarDays, ListChecks, CreditCard, Globe, KeyRound, Lightbulb,
  HelpCircle, Paperclip, Image as ImageIcon,
} from 'lucide-react';

import { supportService } from '../../services/supportService';
import { describeApiError } from '../../services/apiErrors';
import { captureContext } from '../../utils/supportContext';
import { TICKET_CATEGORIES } from '../../utils/supportFormat';
import SupportAssistant from '../../components/help/SupportAssistant';

/**
 * Create a ticket. The customer picks what it's about and writes a title and
 * a description -- optionally a screenshot or a file. Everything else (page,
 * URL, browser, device, organization, plan, time) is captured and shown, so
 * they can see what we'll receive without having to type any of it.
 *
 * Before they send, articles matching the title are offered: the cheapest
 * ticket is the one the Knowledge Base answered.
 */
const ICONS = {
  bug: Bug, technical: Wrench, attendance: Clock3, leave: CalendarDays, task: ListChecks,
  billing: CreditCard, domain: Globe, login: KeyRound, feature_request: Lightbulb, other: HelpCircle,
};
const CATEGORIES = TICKET_CATEGORIES.map(([key, label, hint]) => ({ key, label, hint, Icon: ICONS[key] }));
const KNOWN = new Set(CATEGORIES.map((c) => c.key));
// Older links used ?kind=problem|contact|feature.
const FROM_KIND = { problem: 'bug', contact: 'other', feature: 'feature_request' };

const Support = () => {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const from = params.get('from') || '';
  const initial = params.get('category') || FROM_KIND[params.get('kind')] || '';
  const [category, setCategory] = useState(KNOWN.has(initial) ? initial : '');
  const [form, setForm] = useState({ subject: params.get('subject') || '', message: '' });
  const [files, setFiles] = useState({ screenshot: null, attachment: null });
  const [state, setState] = useState({ busy: false, error: '' });
  const context = useMemo(() => captureContext(from || undefined), [from]);

  const submit = async (event) => {
    event.preventDefault();
    setState({ busy: true, error: '' });
    try {
      const data = await supportService.createTicket(
        { category, subject: form.subject, message: form.message, from: from || undefined },
        files,
      );
      navigate(category === 'feature_request' ? '/help/features' : `/help/tickets/${data.id}`,
        { state: { notice: data.detail } });
    } catch (e) {
      const d = e?.response?.data || {};
      setState({ busy: false,
        error: d.message || d.screenshot?.[0] || d.attachment?.[0]
          || describeApiError(e, 'We couldn’t send that. Please try again.') });
    }
  };

  const isFeature = category === 'feature_request';
  return (
    <div className="page hc sc">
      <h1 className="hc-h1">{isFeature ? 'Suggest a feature' : 'Create a ticket'}</h1>
      <p className="hc-muted">
        Tell us what’s wrong — we’ll take care of the details. You’ll hear back here and by email.
      </p>

      <fieldset className="sc-cats">
        <legend className="sc-legend">What is it about?</legend>
        {CATEGORIES.map(({ key, label, Icon, hint }) => (
          <button key={key} type="button" aria-pressed={category === key}
                  className={`sp-kind${category === key ? ' is-on' : ''}`} onClick={() => setCategory(key)}>
            <Icon size={18} aria-hidden="true" />
            <strong>{label}</strong>
            <small>{hint}</small>
          </button>
        ))}
      </fieldset>

      <form className="sp-form" onSubmit={submit}>
        <label className="acc-field">
          <span>Title</span>
          <input value={form.subject} maxLength={200} required
                 onChange={(e) => setForm({ ...form, subject: e.target.value })}
                 placeholder={isFeature ? 'e.g. Export attendance to Excel' : 'e.g. The Check in button is missing'} />
        </label>

        {!isFeature && (
          <SupportAssistant subject={form.subject} message={form.message} category={category}
                            onSolved={(source) => {
                              supportService.deflected(source || 'create-ticket');
                              navigate('/help', { state: { notice: 'Glad that helped — no ticket was opened.' } });
                            }} />
        )}

        <label className="acc-field">
          <span>{isFeature ? 'What would it help you do?' : 'What happened, and what did you expect?'}</span>
          <textarea rows={6} required value={form.message}
                    onChange={(e) => setForm({ ...form, message: e.target.value })} />
        </label>

        {!isFeature && (
          <div className="sc-files">
            <label className="sc-file">
              <ImageIcon size={15} aria-hidden="true" />
              <span>{files.screenshot ? files.screenshot.name : 'Add a screenshot'}</span>
              <input type="file" accept="image/png,image/jpeg,image/gif,image/webp"
                     onChange={(e) => setFiles({ ...files, screenshot: e.target.files?.[0] || null })} />
            </label>
            <label className="sc-file">
              <Paperclip size={15} aria-hidden="true" />
              <span>{files.attachment ? files.attachment.name : 'Attach a file'}</span>
              <input type="file" accept=".pdf,.png,.jpg,.jpeg,.txt,.docx,.xlsx,.csv"
                     onChange={(e) => setFiles({ ...files, attachment: e.target.files?.[0] || null })} />
            </label>
            <span className="hc-muted">Up to 10 MB each.</span>
          </div>
        )}

        <details className="sc-context">
          <summary>We’ll attach these details automatically</summary>
          <dl>
            <dt>Page</dt><dd>{context.page}</dd>
            <dt>Browser</dt><dd>{context.browser} on {context.os}</dd>
            <dt>Device</dt><dd>{context.device} · {context.viewport}</dd>
            <dt>Time</dt><dd>{new Date(context.client_time).toLocaleString()}</dd>
            <dt>Also</dt><dd>Your name, organization and plan</dd>
          </dl>
        </details>

        {state.error && <p className="acc-err" role="alert">{state.error}</p>}
        <button type="submit" className="btn btn-primary"
                disabled={state.busy || !category || !form.subject.trim() || !form.message.trim()}>
          {state.busy ? 'Sending…' : isFeature ? 'Send suggestion' : 'Create ticket'}
        </button>
        {!category && <p className="hc-muted">Choose what it’s about first.</p>}
      </form>
    </div>
  );
};

export default Support;
