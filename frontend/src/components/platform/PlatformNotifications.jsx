import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertTriangle, Building2, CheckCircle2, CreditCard, Globe, Loader2,
} from 'lucide-react';

import { platformService } from '../../services/platformService';

/**
 * What needs an operator, as a dropdown.
 *
 * DERIVED, NOT STORED. There is no notifications table for platform staff
 * and this does not add one. Every item below is a condition the console can
 * already see — a payment waiting, a tenant stuck provisioning, a domain
 * that will not verify, a subscription about to lapse — read from the same
 * endpoints the dashboard uses.
 *
 * That is a deliberate trade and worth stating. A stored feed would survive
 * a refresh, support read/unread and could be delivered by email; it would
 * also be a second copy of the truth that can disagree with the pages it
 * links to, and "the bell said three but the queue shows none" is the
 * failure that teaches people to stop opening the bell. Until there is a
 * reason to need read state, the queue IS the notification.
 *
 * Ordered by who is inconvenienced: money waiting on us, then a customer
 * who cannot work, then work for office hours.
 */
const Row = ({ to, icon, title, detail, tone = '' }) => (
  <Link className={`pf-note-row${tone ? ` is-${tone}` : ''}`} to={to}>
    <span className="pf-note-ic" aria-hidden="true">{icon}</span>
    <span className="pf-note-text">
      <strong>{title}</strong>
      {detail && <small>{detail}</small>}
    </span>
  </Link>
);

const PlatformNotifications = ({ onClose }) => {
  const [state, setState] = useState(null);

  useEffect(() => {
    let alive = true;
    Promise.all([
      platformService.dashboard().catch(() => ({ data: {} })),
      platformService.health().catch(() => ({ data: {} })),
      platformService.domains().catch(() => ({ data: { domains: [] } })),
    ])
      .then(([dash, health, domains]) => {
        if (!alive) return;
        setState({
          dash: dash.data || {},
          health: health.data || {},
          domains: (domains.data?.domains || []),
        });
      });
    return () => { alive = false; };
  }, []);

  if (!state) {
    return (
      <div className="pf-menu pf-notes" role="status">
        <div className="pf-note-empty">
          <Loader2 size={16} className="pf-spin" aria-hidden="true" />
          Checking the platform…
        </div>
      </div>
    );
  }

  const { dash, health, domains } = state;
  const items = [];

  if (dash.payments_pending_verification > 0) {
    items.push(
      <Row key="pay" to="/platform/payments" tone="urgent"
           icon={<CreditCard size={15} />}
           title={`${dash.payments_pending_verification} payment${dash.payments_pending_verification === 1 ? '' : 's'} awaiting review`}
           detail="A customer has paid and is waiting on us." />,
    );
  }

  const stuck = health.organizations_stuck_provisioning || [];
  if (stuck.length) {
    items.push(
      <Row key="prov" to="/platform/organizations" tone="urgent"
           icon={<AlertTriangle size={15} />}
           title={`${stuck.length} tenant${stuck.length === 1 ? '' : 's'} stuck provisioning`}
           detail={stuck.slice(0, 3).join(', ')} />,
    );
  }

  const drift = Object.keys(health.subscription_mirror_drift || {}).length;
  if (drift) {
    items.push(
      <Row key="drift" to="/platform/health" tone="warn"
           icon={<AlertTriangle size={15} />}
           title={`${drift} subscription mirror${drift === 1 ? '' : 's'} drifted`}
           detail="Platform health → Correct mirrors repairs it." />,
    );
  }

  const failing = domains.filter(
    (d) => d.status === 'failed' || (d.status === 'pending' && d.check_count >= 3));
  if (failing.length) {
    items.push(
      <Row key="dom" to="/platform/domains" tone="warn"
           icon={<Globe size={15} />}
           title={`${failing.length} custom domain${failing.length === 1 ? '' : 's'} not verifying`}
           detail={failing.slice(0, 2).map((d) => d.hostname).join(', ')} />,
    );
  }

  if (dash.subscriptions_expiring_30d > 0) {
    items.push(
      <Row key="exp" to="/platform/subscriptions"
           icon={<Building2 size={15} />}
           title={`${dash.subscriptions_expiring_30d} subscription${dash.subscriptions_expiring_30d === 1 ? '' : 's'} expiring within 30 days`}
           detail="Chase the renewal before it becomes a suspension." />,
    );
  }

  return (
    <div className="pf-menu pf-notes" role="region" aria-label="Notifications">
      <div className="pf-notes-head">
        <strong>Needs attention</strong>
        {items.length > 0 && <span className="pf-notes-count">{items.length}</span>}
      </div>
      {items.length === 0 ? (
        <div className="pf-note-empty pf-note-clear">
          <CheckCircle2 size={16} aria-hidden="true" />
          {/* An empty state that says what was checked. "Nothing here" alone
              reads as "not loaded yet". */}
          Nothing is waiting. Payments, provisioning, domains and renewals
          are all clear.
        </div>
      ) : items}
      <Link className="pf-notes-foot" to="/platform/audit" onClick={onClose}>
        View the full activity log
      </Link>
    </div>
  );
};

export default PlatformNotifications;
