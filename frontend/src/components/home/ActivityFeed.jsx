import React from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Bell } from 'lucide-react';
import { notificationService } from '../../services/notificationService';
import {
  collapseRows, groupByDay, initialsFrom, truncate,
} from './feed';

/**
 * Latest updates (Phase 203 / blueprint §03, widget 9 — redesigned).
 *
 * Built from /notifications/, which is already per-user and already carries an
 * action_url. The organisation-wide audit log is NOT used here: AuditLogViewSet
 * is admin-only, and a feed that silently showed nothing to most of the staff
 * would be worse than one built from a source everybody has.
 *
 * This is not a second notification centre. The bell keeps unread state and
 * interruption; this is the read-only history of what happened, with no badge.
 */

const timeOf = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
};

// Five rows on a desktop, three on a phone (the caller decides). The full
// list lives at /notifications.
const ActivityFeed = ({ limit = 5 }) => {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['notifications', 'recent', limit],
    queryFn: () => notificationService.list({ page_size: limit }),
    staleTime: 60_000,
    retry: false,
  });

  const rows = collapseRows((data ?? []).slice(0, limit));
  const groups = groupByDay(rows);

  return (
    <section className="hm-sec" aria-labelledby="hm-feed-h">
      <div className="hm-sec-h">
        <h2 id="hm-feed-h">Latest updates</h2>
        <Link to="/notifications" className="hm-sec-link">View all →</Link>
      </div>

      <div className="hm-card hm-feed">
        {isLoading ? (
          <ul className="hm-feed-list" aria-busy="true" aria-hidden="true">
            {[['64%', '88%'], ['48%', '72%'], ['56%', '80%']].map(([t, b], i) => (
              <li className="hm-feed-row is-skeleton" key={i}>
                <span className="hm-skel hm-skel-av" />
                <span className="hm-feed-main">
                  <span className="hm-skel" style={{ width: t }} />
                  <span className="hm-skel hm-skel-sm" style={{ width: b }} />
                </span>
                <span className="hm-skel hm-skel-sm" style={{ width: 36 }} />
              </li>
            ))}
          </ul>
        ) : isError ? (
          <p className="hm-quiet">We couldn’t load your updates just now.</p>
        ) : rows.length === 0 ? (
          <p className="hm-quiet">You’re all caught up. Updates about your leave, tasks and documents will appear here.</p>
        ) : (
          groups.map((g) => (
            <div key={g.label} className="hm-feed-day">
              <h3 className="hm-feed-dl">{g.label}</h3>
              <ul className="hm-feed-list">
                {g.rows.map((n) => {
                  const initials = initialsFrom(n.title, n.body);
                  const text = (
                    <>
                      <span className="hm-feed-t">
                        {n.title}
                        {n.count > 1 && <span className="hm-feed-x" aria-label={`${n.count} similar`}>×{n.count}</span>}
                      </span>
                      {n.body && <span className="hm-feed-b">{truncate(n.body)}</span>}
                    </>
                  );
                  return (
                    <li key={n.id} className="hm-feed-row">
                      <span className="hm-av" aria-hidden="true">
                        {initials || <Bell size={14} strokeWidth={1.75} />}
                      </span>
                      {n.action_url
                        ? <Link to={n.action_url} className="hm-feed-main">{text}</Link>
                        : <span className="hm-feed-main">{text}</span>}
                      <span className="hm-feed-time">{timeOf(n.created_at)}</span>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))
        )}
      </div>
    </section>
  );
};

export default ActivityFeed;
