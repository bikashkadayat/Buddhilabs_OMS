import React, { useState } from 'react';
import {
  Check, Copy, Eye, EyeOff, Mail, ExternalLink,
} from 'lucide-react';

import { platformService } from '../../services/platformService';
import {
  copyText, credentialsText, fullPackage, loginUrlText, welcomeMessage,
} from '../../utils/handover';

/**
 * Everything a customer needs to get in, ready to hand over.
 *
 * ONE CARD, TWO PLACES. Straight after creation it holds the temporary
 * password, because that is the only moment anybody has it. On the
 * organization page afterwards it holds everything except the password,
 * and the email action issues a fresh one instead — see `tenancy/handover.py`
 * for why the server can do the first without ever storing it.
 *
 * WHAT IT IS FOR. An operator used to read four facts off three parts of the
 * screen and retype them into an email. Each retyping was a chance to send a
 * customer to the wrong hostname on their first day.
 */
const fmtDate = (value) => (value
  ? new Date(value).toLocaleDateString(undefined,
    { day: 'numeric', month: 'short', year: 'numeric' })
  : '');

const CopyButton = ({ label, text, onCopied, primary = false }) => {
  const [done, setDone] = useState(false);
  const run = async () => {
    const ok = await copyText(text);
    setDone(ok);
    onCopied?.(ok ? `${label.replace(/^Copy /, '')} copied.` : 'Your browser blocked the copy. Select the text and copy it by hand.');
    if (ok) setTimeout(() => setDone(false), 1800);
  };
  return (
    <button type="button" className={`btn ${primary ? 'btn-secondary' : 'btn-ghost'} pf-copy`}
            onClick={run} disabled={!text}>
      {done ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
      {done ? 'Copied' : label}
    </button>
  );
};

const HandoverCard = ({ access, password = null, slug, title = 'Access information',
                        onSent }) => {
  const [reveal, setReveal] = useState(false);
  const [status, setStatus] = useState(null);
  const [sending, setSending] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [sent, setSent] = useState(null);

  if (!access) return null;
  const admin = access.administrator;
  const canSend = Boolean(admin?.awaiting_first_sign_in);
  // Without the password in hand, sending means issuing a new one — which
  // invalidates anything given earlier, so it is confirmed first.
  const reissues = !password;

  const send = async () => {
    setSending(true);
    setStatus(null);
    try {
      const { data } = await platformService.sendAccess(slug, password);
      setSent(data);
      setConfirming(false);
      setStatus(data.reissued
        ? `New sign-in details emailed to ${data.sent_to}. The earlier temporary password no longer works.`
        : `Sign-in details emailed to ${data.sent_to}.`);
      onSent?.(data);
    } catch (error) {
      setStatus(error?.response?.data?.detail
        || 'The email could not be sent. Nothing was changed.');
    } finally {
      setSending(false);
    }
  };

  const adminState = () => {
    if (!admin) return null;
    if (admin.awaiting_first_sign_in) {
      return <span className="pf-chip pf-chip-wait">Not signed in yet</span>;
    }
    return (
      <span className="pf-chip pf-chip-ok">
        <Check size={12} aria-hidden="true" />
        Signed in{admin.last_login ? ` · ${fmtDate(admin.last_login)}` : ''}
      </span>
    );
  };

  return (
    <section className="pf-handover" aria-label={title}>
      <header className="pf-handover-head">
        <h2 className="pf-panel-title">{title}</h2>
        {adminState()}
      </header>

      {!access.can_sign_in && (
        <p className="pf-alert" role="alert">
          This workspace is not open right now, so these details will not get
          anyone in until it is reactivated.
        </p>
      )}

      <dl className="pf-handover-grid">
        <dt>Login URL</dt>
        <dd className="pf-handover-url">
          <a href={access.login_url} target="_blank" rel="noreferrer">
            {access.login_url}
            <ExternalLink size={12} aria-hidden="true" />
          </a>
          <CopyButton label="Copy" text={loginUrlText(access)} onCopied={setStatus} />
        </dd>

        {access.custom_domain && (
          <>
            <dt>Their domain</dt>
            <dd>
              {access.custom_domain.hostname}{' '}
              <span className={`pf-chip ${access.custom_domain.serving ? 'pf-chip-ok' : 'pf-chip-wait'}`}>
                {access.custom_domain.serving ? 'Live' : access.custom_domain.status_display}
              </span>
              {!access.custom_domain.serving && (
                <small className="pf-handover-hint">
                  Until DNS is verified, the address above is the one that works.
                </small>
              )}
            </dd>
          </>
        )}

        {admin ? (
          <>
            <dt>Administrator</dt>
            <dd>
              <strong>{admin.name}</strong>
              <span className="pf-handover-sub">{admin.email}</span>
            </dd>
          </>
        ) : (
          <>
            <dt>Administrator</dt>
            <dd className="pf-muted">
              None yet. Add one so the customer has an account to sign in with.
            </dd>
          </>
        )}

        {admin && (
          <>
            <dt>Temporary password</dt>
            <dd>
              {password ? (
                <span className="pf-handover-pass">
                  <code>{reveal ? password : '•'.repeat(12)}</code>
                  <button type="button" className="btn btn-ghost pf-copy"
                          onClick={() => setReveal((r) => !r)}
                          aria-pressed={reveal}>
                    {reveal ? <EyeOff size={14} aria-hidden="true" /> : <Eye size={14} aria-hidden="true" />}
                    {reveal ? 'Hide' : 'Show'}
                  </button>
                </span>
              ) : admin.awaiting_first_sign_in ? (
                <span className="pf-muted">
                  Not kept. Email new sign-in details to give them a fresh one.
                </span>
              ) : (
                <span className="pf-muted">
                  They chose their own password, so there is nothing to send.
                </span>
              )}
            </dd>
          </>
        )}

        {access.plan && (
          <>
            <dt>Plan</dt>
            <dd>{access.plan.name}</dd>
          </>
        )}
        {access.trial && (
          <>
            <dt>Trial</dt>
            <dd>
              {access.trial.days ? `${access.trial.days} days, ` : ''}
              until {fmtDate(`${access.trial.ends_on}T00:00:00`)}
              <span className="pf-handover-sub">
                {access.trial.days_remaining} day{access.trial.days_remaining === 1 ? '' : 's'} left
              </span>
            </dd>
          </>
        )}
      </dl>

      {password && (
        <p className="pf-handover-warn">
          This is the only time the password is shown. Copy or email it before
          you close this window.
        </p>
      )}

      <div className="pf-handover-actions">
        <CopyButton label="Copy login URL" text={loginUrlText(access)} onCopied={setStatus} />
        {admin && (
          <CopyButton label="Copy credentials" text={credentialsText(access, password)}
                      onCopied={setStatus} />
        )}
        {admin && (
          <CopyButton label="Copy welcome message" text={welcomeMessage(access, password)}
                      onCopied={setStatus} />
        )}
        <CopyButton label="Copy full package" text={fullPackage(access, password)}
                    onCopied={setStatus} />
        {/* See what the customer will see: their sign-in page, their brand. */}
        <a className="btn btn-ghost pf-copy" href={access.login_url}
           target="_blank" rel="noreferrer">
          <ExternalLink size={14} aria-hidden="true" /> Open portal
        </a>
      </div>

      {admin && (
        <div className="pf-handover-send">
          {!canSend ? (
            <p className="pf-muted">
              {admin.email} has already signed in and chosen their own
              password, so there is nothing to send. If they forget it,
              another administrator in their workspace can reset it.
            </p>
          ) : confirming ? (
            <div className="pf-handover-confirm" role="group" aria-label="Confirm">
              <p>
                This gives {admin.email} a new temporary password and emails it
                to them. Any password they were given before stops working.
              </p>
              <button type="button" className="btn btn-primary" onClick={send}
                      disabled={sending}>
                <Mail size={14} aria-hidden="true" />
                {sending ? 'Sending…' : 'Yes, email new details'}
              </button>
              <button type="button" className="btn btn-ghost"
                      onClick={() => setConfirming(false)} disabled={sending}>
                Cancel
              </button>
            </div>
          ) : (
            <button type="button" className="btn btn-primary"
                    onClick={() => (reissues ? setConfirming(true) : send())}
                    disabled={sending}>
              <Mail size={14} aria-hidden="true" />
              {sending ? 'Sending…'
                : sent ? 'Send again'
                  : reissues ? 'Email new sign-in details' : `Send welcome email to ${admin.email}`}
            </button>
          )}
        </div>
      )}

      {admin && (
        <details className="pf-handover-preview">
          <summary>Preview the welcome message</summary>
          <pre>{welcomeMessage(access, password && reveal ? password : (password ? '••••••••••••' : null))}</pre>
        </details>
      )}

      {status && <p className="pf-handover-status" role="status">{status}</p>}
    </section>
  );
};

export default HandoverCard;
