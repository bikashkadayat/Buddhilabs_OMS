import React, { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { CheckCircle2, Circle, ArrowRight, Compass, LifeBuoy } from 'lucide-react';

import api from '../services/api';
import { restartTour } from '../services/tours';

/**
 * Getting Started: the administrator's setup, in one place, with progress.
 *
 * Every step is MEASURED from the workspace (tenancy.onboarding): uploading a
 * logo ticks "Upload your logo"; a colleague's first sign-in ticks "Invite
 * your team". A step can be marked done by hand only when it genuinely
 * doesn't apply -- the tick is an override, never the record.
 */
const GettingStarted = () => {
  const [state, setState] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.get('/tenant/onboarding/').then(({ data }) => setState(data))
      .catch(() => setState({ applicable: false }));
  }, []);
  useEffect(load, [load]);

  const act = async (body) => {
    setBusy(true);
    try { setState((await api.post('/tenant/onboarding/', body)).data); } finally { setBusy(false); }
  };

  if (!state) return <div className="page"><p className="hc-muted">Loading…</p></div>;
  if (!state.applicable) {
    return (
      <div className="page gs">
        <h1 className="hc-h1">Getting started</h1>
        <p className="hc-muted">Your administrator sets up the workspace. To learn your way around, see <Link to="/help">Help</Link>.</p>
      </div>
    );
  }

  const { progress, checklist, ready, organization } = state;
  const next = checklist.find((s) => !s.done);
  const circumference = 2 * Math.PI * 34;

  return (
    <div className="page gs">
      <header className="gs-head">
        <div className="gs-ring" role="img" aria-label={`${progress.percent}% set up`}>
          <svg viewBox="0 0 80 80" aria-hidden="true">
            <circle cx="40" cy="40" r="34" className="gs-ring-bg" />
            <circle cx="40" cy="40" r="34" className="gs-ring-fg"
                    strokeDasharray={circumference}
                    strokeDashoffset={circumference * (1 - progress.percent / 100)} />
          </svg>
          <b>{progress.percent}%</b>
        </div>
        <div>
          <h1 className="hc-h1">Getting started with {organization.name}</h1>
          <p className="hc-muted">
            {progress.completed === progress.total
              ? 'All set. Your workspace is ready for everyone.'
              : `${progress.completed} of ${progress.total} done. Each step ticks itself as you complete it.`}
          </p>
          {next && (
            <Link className="btn btn-primary gs-next" to={next.action}>
              Next: {next.title} <ArrowRight size={15} aria-hidden="true" />
            </Link>
          )}
        </div>
      </header>

      <ol className="gs-steps">
        {checklist.map((step, i) => (
          <li key={step.key} className={step.done ? 'is-done' : ''}>
            <span className="gs-mark" aria-hidden="true">
              {step.done ? <CheckCircle2 size={22} /> : <Circle size={22} />}
            </span>
            <div className="gs-body">
              <span className="gs-n">Step {i + 1}</span>
              <Link to={step.action} className="gs-title">{step.title}</Link>
              <p>{step.detail}</p>
            </div>
            {!step.done && (
              <div className="gs-acts">
                <Link className="btn btn-ghost btn-sm" to={step.action}>Do it</Link>
                <button type="button" className="gs-skip" disabled={busy}
                        onClick={() => act({ step_done: step.key })}>
                  Doesn’t apply to us
                </button>
              </div>
            )}
          </li>
        ))}
      </ol>

      <section className="gs-ready" aria-labelledby="gs-ready-h">
        <h2 id="gs-ready-h">Already set up for you</h2>
        <ul>
          {ready.map((item) => (
            <li key={item.key} className={item.ready ? 'is-ok' : 'is-bad'}>
              {item.ready ? <CheckCircle2 size={15} aria-hidden="true" /> : <Circle size={15} aria-hidden="true" />}
              {item.label}
            </li>
          ))}
        </ul>
      </section>

      <p className="gs-help">
        <button type="button" className="lg-link-btn" onClick={restartTour}>
          <Compass size={14} aria-hidden="true" /> Take the tour
        </button>
        <Link to="/help"><LifeBuoy size={14} aria-hidden="true" /> Help & how-tos</Link>
      </p>
    </div>
  );
};

export default GettingStarted;
