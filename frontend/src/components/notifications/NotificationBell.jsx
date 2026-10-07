import React, { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Bell, CheckCheck } from 'lucide-react';
import { notificationService } from '../../services/notificationService';

const NotificationBell = () => {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  // Poll the unread count every 30s.
  const { data: unread = 0 } = useQuery({
    queryKey: ['notif-unread'],
    queryFn: notificationService.unreadCount,
    refetchInterval: 30000,
    refetchOnWindowFocus: true,
  });

  const { data: recent = [] } = useQuery({
    queryKey: ['notif-recent'],
    queryFn: () => notificationService.list({ page_size: 5 }),
    enabled: open,
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['notif-unread'] });
    qc.invalidateQueries({ queryKey: ['notif-recent'] });
    // The full page keeps its own list; clearing the badge without clearing
    // that leaves the two disagreeing on the same screen.
    qc.invalidateQueries({ queryKey: ['notifications'] });
  };

  const markRead = useMutation({
    mutationFn: (id) => notificationService.markRead(id),
    onSuccess: refresh,
  });

  const markAllRead = useMutation({
    mutationFn: notificationService.markAllRead,
    onSuccess: refresh,
  });

  useEffect(() => {
    const onClick = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, []);

  const openItem = (n) => {
    if (!n.is_read) markRead.mutate(n.id);
    setOpen(false);
    if (n.action_url) navigate(n.action_url);
  };

  return (
    <div className="lr-bell" ref={ref}>
      <button
        type="button" className="lr-bell-btn" data-tour="notifications" aria-label={`Notifications${unread ? `, ${unread} unread` : ''}`}
        aria-haspopup="true" aria-expanded={open} onClick={() => setOpen((o) => !o)}
      >
        <Bell size={20} />
        {unread > 0 && <span className="lr-bell-badge" aria-hidden="true">{unread > 9 ? '9+' : unread}</span>}
      </button>

      {open && (
        <div className="lr-bell-menu" role="menu" aria-label="Recent notifications">
          <div className="lr-bell-head">
            <span>Notifications</span>
            {/* Only offered when there is something to mark. A button that is
                always present but does nothing most of the time teaches people
                to ignore it, and the count is already the signal. */}
            {unread > 0 && (
              <button
                type="button"
                className="lr-bell-markall"
                disabled={markAllRead.isPending}
                /* The dropdown closes on mousedown outside it; this button is
                   inside, but stopping propagation keeps a future change to
                   that handler from closing the menu out from under the click. */
                onClick={(e) => { e.stopPropagation(); markAllRead.mutate(); }}
              >
                <CheckCheck size={13} aria-hidden="true" />
                {markAllRead.isPending ? 'Marking…' : 'Mark all as read'}
              </button>
            )}
          </div>
          {recent.length === 0 ? (
            <div className="lr-bell-empty">You're all caught up.</div>
          ) : recent.slice(0, 5).map((n) => (
            <button
              key={n.id} type="button" role="menuitem"
              className={`lr-bell-item ${n.is_read ? '' : 'unread'}`} onClick={() => openItem(n)}
            >
              <div className="lr-bell-title">{n.title}</div>
              {n.body && <div className="lr-bell-body">{n.body}</div>}
              <div className="lr-bell-time">{new Date(n.created_at).toLocaleString()}</div>
            </button>
          ))}
          <button type="button" className="lr-bell-all" onClick={() => { setOpen(false); navigate('/notifications'); }}>
            See all notifications
          </button>
        </div>
      )}
    </div>
  );
};

export default NotificationBell;
