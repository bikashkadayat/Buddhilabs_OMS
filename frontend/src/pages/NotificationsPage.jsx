import React, { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Check, Trash2 } from 'lucide-react';
import { notificationService } from '../services/notificationService';
import { Skeleton, EmptyState, ErrorState } from '../components/leave-records/States';

// Modules, not categories: the API filters on the MODULE_ prefix, which is the
// same rule the email audit log uses to classify a send.
const GROUP_TABS = [
  ['', 'All'],
  ['approvals', 'Approvals'],
  ['attendance', 'Attendance'],
  ['leave', 'Leave'],
  ['tasks', 'Tasks'],
  ['documents', 'Documents'],
  ['payments', 'Payments'],
];

const PreferencesPanel = () => {
  const qc = useQueryClient();
  const { data: prefs = [], isLoading } = useQuery({ queryKey: ['notif-prefs'], queryFn: notificationService.getPreferences });
  const save = useMutation({
    mutationFn: (payload) => notificationService.setPreference(payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['notif-prefs'] }),
  });

  const toggle = (pref, key) => save.mutate({ ...pref, [key]: !pref[key] });

  if (isLoading) return <Skeleton rows={2} />;
  return (
    <div className="lr-table-wrap">
      <table className="lr-table">
        <thead><tr><th scope="col">Category</th><th scope="col">In-app</th><th scope="col">Email</th></tr></thead>
        <tbody>
          {prefs.map((p) => (
            <tr key={p.category}>
              <td>{p.category_label}</td>
              {/* Wrapped in a <label>, not merely aria-labelled. Phase 100 sized
                  checkboxes at 24px rather than 44 on the explicit grounds that the
                  associated label carries the touch target; an aria-label names the
                  control for a screen reader but creates no CLICK target, so without
                  this wrapper the whole affordance was a 24px box — which the Phase
                  100.1 probe caught here, across all 65 rows.

                  The aria-label stays on the input and there is no visible or
                  sr-only text inside: the hit area comes from the <label> element
                  itself. A first attempt did add an .sr-only span, and it widened
                  the PAGE — .sr-only is position:absolute with no positioned
                  ancestor, so its containing block is the root rather than the
                  table's horizontal scroll container, and it escaped the scroll to
                  push documentElement.scrollWidth to 482px on a 390px screen. */}
              <td>
                <label className="lr-check">
                  <input type="checkbox" checked={p.in_app_enabled} onChange={() => toggle(p, 'in_app_enabled')} aria-label={`In-app ${p.category_label}`} />
                </label>
              </td>
              <td>
                <label className="lr-check">
                  <input type="checkbox" checked={p.email_enabled} onChange={() => toggle(p, 'email_enabled')} aria-label={`Email ${p.category_label}`} />
                </label>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

const NotificationsPage = () => {
  const navigate = useNavigate();
  const qc = useQueryClient();
  // One place for everything, sorted the way people think about it --
  // server-side groups (notifications.views.GROUPS), so page two is never
  // silently filtered away.
  const [group, setGroup] = useState('');
  const [unreadOnly, setUnreadOnly] = useState(false);
  // `?tab=prefs` opens Preferences directly: the account menu links there.
  const [params] = useSearchParams();
  const [tab, setTab] = useState(params.get('tab') === 'prefs' ? 'prefs' : 'inbox');

  // All three filters are SERVER-side. Filtering the fetched page in the
  // browser would quietly hide anything on page two, which is worse than no
  // filter — a person would conclude the notification did not exist.
  const { data: items = [], isLoading, isError, error, refetch } = useQuery({
    queryKey: ['notifications', group, unreadOnly],
    queryFn: () => notificationService.list({
      ...(group ? { group } : {}),
      ...(unreadOnly ? { is_read: 'false' } : {}),
    }),
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['notifications'] });
    qc.invalidateQueries({ queryKey: ['notif-unread'] });
  };
  const markRead = useMutation({ mutationFn: (id) => notificationService.markRead(id), onSuccess: invalidate });
  const markAll = useMutation({ mutationFn: () => notificationService.markAllRead(), onSuccess: invalidate });
  const remove = useMutation({ mutationFn: (id) => notificationService.remove(id), onSuccess: invalidate });

  const openItem = (n) => {
    if (!n.is_read) markRead.mutate(n.id);
    if (n.action_url) navigate(n.action_url);
  };

  return (
    <div className="page" style={{ paddingBottom: 80 }}>
      <div className="lr-page-head">
        <div><h2>Notifications</h2><div className="lr-page-sub">Approvals, attendance, leave, tasks, documents and payments — in one place.</div></div>
        {tab === 'inbox' && <button type="button" className="lr-btn lr-btn-ghost" onClick={() => markAll.mutate()}><Check size={14} /> Mark all read</button>}
      </div>

      <div className="lr-tabs" style={{ marginBottom: 16 }}>
        <button type="button" role="tab" aria-selected={tab === 'inbox'} className={`lr-tab ${tab === 'inbox' ? 'on' : ''}`} onClick={() => setTab('inbox')}>Inbox</button>
        <button type="button" role="tab" aria-selected={tab === 'prefs'} className={`lr-tab ${tab === 'prefs' ? 'on' : ''}`} onClick={() => setTab('prefs')}>Preferences</button>
      </div>

      {tab === 'prefs' ? <PreferencesPanel /> : (
        <>
          <div className="nc-groups" role="tablist" aria-label="Show">
            {GROUP_TABS.map(([value, label]) => (
              <button key={value} type="button" role="tab" aria-selected={group === value}
                      className={`nc-group${group === value ? ' is-on' : ''}`}
                      onClick={() => setGroup(value)}>
                {label}
              </button>
            ))}
            <button
              type="button"
              className={`nc-group nc-unread${unreadOnly ? ' is-on' : ''}`}
              aria-pressed={unreadOnly}
              onClick={() => setUnreadOnly((v) => !v)}
            >
              Unread only
            </button>
          </div>

          {isLoading && <Skeleton rows={4} />}
          {isError && <ErrorState error={error} onRetry={refetch} />}
          {!isLoading && !isError && items.length === 0 && (
            <EmptyState message={group || unreadOnly
              ? 'Nothing here. You’re all caught up.'
              : 'No notifications yet. Approvals, decisions and reminders will appear here as they happen.'}
                        ctaTo={undefined} />
          )}

          {!isLoading && !isError && items.length > 0 && (
            <div className="lr-notif-list">
              {items.map((n) => (
                <div key={n.id} className={`lr-notif-row ${n.is_read ? '' : 'unread'}`}>
                  <button type="button" className="lr-notif-main" onClick={() => openItem(n)}>
                    <div className="lr-notif-cat">{n.category_label}</div>
                    <div className="lr-notif-title">{n.title}</div>
                    {n.body && <div className="lr-notif-body">{n.body}</div>}
                    <div className="lr-notif-time">{new Date(n.created_at).toLocaleString()}</div>
                  </button>
                  <div className="lr-notif-actions">
                    {!n.is_read && <button type="button" className="lr-btn lr-btn-ghost" onClick={() => markRead.mutate(n.id)} aria-label="Mark read"><Check size={14} /></button>}
                    <button type="button" className="lr-btn lr-btn-ghost" onClick={() => remove.mutate(n.id)} aria-label="Delete"><Trash2 size={14} /></button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default NotificationsPage;
