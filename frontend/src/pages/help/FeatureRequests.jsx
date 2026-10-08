import React, { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { Lightbulb, CheckCircle2 } from 'lucide-react';

import EmptyState from '../../components/common/EmptyState';
import { supportService } from '../../services/supportService';
import { when } from '../../utils/supportFormat';

/**
 * Feature requests from your organization, and where each one stands:
 * Submitted → Under review → Planned → In development → Completed.
 * Shown as a step track so "Planned" visibly means more than "Submitted".
 */
const FeatureRequests = () => {
  const location = useLocation();
  const [data, setData] = useState(null);
  useEffect(() => {
    supportService.featureRequests().then(setData).catch(() => setData({ roadmap: [], requests: [] }));
  }, []);
  const steps = data?.roadmap || [];

  return (
    <div className="page hc sc">
      <div className="sc-head">
        <h1 className="hc-h1">Feature requests</h1>
        <Link className="btn btn-primary btn-sm" to="/help/contact?category=feature_request">
          <Lightbulb size={14} aria-hidden="true" /> Suggest a feature
        </Link>
      </div>
      <p className="hc-muted">Ideas and integrations your organization has asked for. We update each one as it moves.</p>
      {location.state?.notice && (
        <p className="sp-ok" role="status"><CheckCircle2 size={16} aria-hidden="true" /> {location.state.notice}</p>
      )}
      {data === null ? <p className="hc-muted">Loading…</p> : data.requests.length === 0 ? (
        <EmptyState title="Your organization hasn’t suggested anything yet."
                    body="Missing a report, an integration, or a faster way to do something? Tell us."
                    action={{ to: '/help/contact?category=feature_request', label: 'Suggest a feature' }} />
      ) : (
        <ul className="sc-list">
          {data.requests.map((r) => {
            const at = steps.findIndex((s) => s.value === r.roadmap_status);
            return (
              <li key={r.id} className="sc-feature">
                <Link to={`/help/tickets/${r.id}`} className="sc-feature-title">
                  {r.unread && <span className="sc-dot" aria-label="Updated" />}
                  <strong>{r.subject || r.message.slice(0, 70)}</strong>
                  <small className="hc-muted">{r.reference} · {r.submitted_by_name} · {when(r.created_at)}</small>
                </Link>
                <ol className="sc-track" aria-label={`Status: ${r.roadmap_status_display}`}>
                  {steps.map((s, i) => (
                    <li key={s.value} className={i <= at ? 'is-done' : ''} aria-current={i === at ? 'step' : undefined}>
                      {s.label}
                    </li>
                  ))}
                </ol>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
};

export default FeatureRequests;
