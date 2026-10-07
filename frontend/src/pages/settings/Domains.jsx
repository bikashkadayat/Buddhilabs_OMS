import React, { useCallback, useEffect, useState } from 'react';
import PageHeader from '../../components/common/PageHeader';
import Skeleton from '../../components/common/Skeleton';
import EmptyState from '../../components/common/EmptyState';
import StatusBadge from '../../components/common/StatusBadge';
import { brandingService } from '../../services/brandingService';

/**
 * Phase S9 Part 3: Settings → Custom domain.
 *
 * THE PAGE EXISTS TO MAKE ONE THING UNMISTAKABLE: there are TWO DNS records
 * and they do different jobs. The verification record proves the domain is
 * yours; the serving record is what actually sends visitors here. Every
 * support conversation about custom domains is somebody who published one of
 * them and expected both, so both are shown, side by side, labelled.
 *
 * NOTHING HERE GRANTS A DOMAIN. Claiming issues a token and changes nothing
 * about how the platform resolves hostnames -- the address does not work
 * until DNS answers with the proof. That is deliberate and is stated on the
 * page, because a customer who thinks claiming was enough will report the
 * working product as broken.
 */
const STATUS_LABEL = {
  pending: 'Awaiting DNS',
  verified: 'Verified',
  active: 'Live',
  failed: 'Not found',
  removed: 'Withdrawn',
};

const STATUS_WORDS = {
  pending: 'Waiting for your DNS record. Publish the records below, then '
    + 'press “Check now”. DNS changes can take up to an hour to spread.',
  verified: 'Verified. Traffic is not being served on it yet.',
  active: 'Live. Your team can sign in at this address.',
  failed: 'We looked and could not find the record. Check it against the '
    + 'values below — the name often needs your domain appended, or not, '
    + 'depending on your DNS provider.',
  removed: 'Withdrawn.',
};

const Domains = () => {
  const [rows, setRows] = useState(null);
  const [hostname, setHostname] = useState('');
  const [method, setMethod] = useState('txt');
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => brandingService.domains()
    .then((response) => setRows(response.data))
    .catch(() => { setRows([]); setError('Your domains could not be loaded.'); }),
  []);

  useEffect(() => { load(); }, [load]);

  const claim = async (event) => {
    event.preventDefault();
    setBusy(true); setError(null); setNotice(null);
    try {
      await brandingService.claimDomain(hostname.trim(), method);
      setHostname('');
      await load();
      setNotice('Claimed. Publish the two records below, then check it.');
    } catch (err) {
      setError(err?.response?.data?.detail
        || err?.response?.data?.hostname?.[0]
        || 'That address could not be claimed.');
    } finally { setBusy(false); }
  };

  const check = async (host) => {
    setBusy(true); setError(null); setNotice(null);
    try {
      const response = await brandingService.verifyDomain(host);
      await load();
      setNotice(response.data?.status === 'active'
        ? `${host} is verified and live.`
        : `${host} is not verified yet. ${response.data?.last_error || ''}`);
    } catch (err) {
      // 503 means we could not look, which is not the customer's fault and
      // must not be reported as a missing record.
      setError(err?.response?.status === 503
        ? 'We cannot check DNS at the moment. Nothing is wrong with your '
          + 'records — please try again shortly or contact support.'
        : (err?.response?.data?.detail || 'That check could not be run.'));
    } finally { setBusy(false); }
  };

  const remove = async (host) => {
    setBusy(true); setError(null); setNotice(null);
    try {
      await brandingService.removeDomain(host);
      await load();
      setNotice(`${host} withdrawn. It no longer reaches your workspace.`);
    } catch (err) {
      setError(err?.response?.data?.detail || 'That could not be withdrawn.');
    } finally { setBusy(false); }
  };

  if (rows === null) {
    return (
      <div className="page">
        <PageHeader title="Custom domain" description="Use your own web address." />
        <Skeleton rows={4} />
      </div>
    );
  }

  return (
    <div className="page">
      <PageHeader
        title="Custom domain"
        description="Sign in at your own address instead of ours."
      />

      {error && <div className="br-alert br-alert-bad" role="alert">{error}</div>}
      {notice && <div className="br-alert br-alert-ok" role="status">{notice}</div>}

      <section className="br-card">
        <h2 className="br-h">Add an address</h2>
        <p className="br-lede">
          Something like <code>hr.yourcompany.com</code>. You will need access
          to your DNS settings to prove the domain is yours — nothing changes
          until you do.
        </p>
        <form className="br-claim" onSubmit={claim}>
          <label className="br-label" htmlFor="dm-host">Address</label>
          <input
            id="dm-host"
            className="br-input br-input-wide"
            type="text"
            value={hostname}
            onChange={(event) => setHostname(event.target.value)}
            placeholder="hr.yourcompany.com"
            required
            aria-describedby="dm-host-hint"
          />
          <p className="br-hint" id="dm-host-hint">
            Letters, digits and hyphens. Do not include https:// or a path.
          </p>
          <label className="br-label" htmlFor="dm-method">
            How would you like to prove it?
          </label>
          <select
            id="dm-method"
            className="br-input"
            value={method}
            onChange={(event) => setMethod(event.target.value)}
          >
            <option value="txt">A TXT record (most common)</option>
            <option value="cname">A CNAME record</option>
          </select>
          <button type="submit" className="br-save" disabled={busy || !hostname.trim()}>
            {busy ? 'Working…' : 'Claim this address'}
          </button>
        </form>
      </section>

      {rows.length === 0 ? (
        <EmptyState
          variant="first"
          title="No custom address yet"
          body="Your workspace is reachable at the address we gave you. Add your own above when you are ready."
        />
      ) : rows.map((row) => (
        <section className="br-card" key={row.hostname}>
          <div className="br-domain-head">
            <h2 className="br-h">{row.hostname}</h2>
            {/* `failed` is not in StatusBadge's own map, so the label is
                passed: "Not found" is the honest word -- the check ran and
                the record was not there, which is different from an error. */}
            <StatusBadge
              status={row.status}
              label={STATUS_LABEL[row.status]}
            />
          </div>
          <p className="br-lede">{STATUS_WORDS[row.status] || row.status}</p>
          {row.last_error && row.status === 'failed' && (
            <p className="br-hint br-warn">{row.last_error}</p>
          )}

          <h3 className="br-h3">1. Prove the domain is yours</h3>
          <dl className="br-record">
            <dt>Type</dt><dd>{row.record_type}</dd>
            <dt>Name</dt><dd><code>{row.record_name}</code></dd>
            <dt>Value</dt><dd><code>{row.record_value}</code></dd>
          </dl>

          <h3 className="br-h3">2. Send visitors here</h3>
          <p className="br-hint">{row.serving_record?.note}</p>
          <dl className="br-record">
            <dt>Type</dt><dd>{row.serving_record?.type}</dd>
            <dt>Name</dt><dd><code>{row.serving_record?.name}</code></dd>
            <dt>Value</dt><dd><code>{row.serving_record?.value}</code></dd>
          </dl>

          <div className="br-actions">
            <button
              type="button"
              className="br-save"
              onClick={() => check(row.hostname)}
              disabled={busy}
            >
              Check now
            </button>
            <button
              type="button"
              className="br-cancel"
              onClick={() => remove(row.hostname)}
              disabled={busy}
            >
              Withdraw
            </button>
          </div>
        </section>
      ))}
    </div>
  );
};

export default Domains;
