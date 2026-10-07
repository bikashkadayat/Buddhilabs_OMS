import React from 'react';
import { Link } from 'react-router-dom';
import { CheckCircle2, Megaphone } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { circularService } from '../../services/circularService';

/**
 * Circulars broadcast to you that you have not opened (blueprint §03, widget 8).
 *
 * Separate from the acknowledgement rows in the queue, because opening a
 * circular and confirming it are separate facts - one the system observes, one
 * the person states. The queue carries the second; this carries the first.
 *
 * ONE LINE. Up to three subjects, each a link, and a way to the rest. A full
 * card to carry "you have read everything" was a third of a phone screen spent
 * on the absence of news — and deliberately NOT rendered as nothing, because
 * an empty space cannot be told apart from a widget that failed to mount.
 */
const UnreadNotices = () => {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['circulars', 'unread', 'home'],
    queryFn: () => circularService.getCirculars({ scope: 'unread' }),
    staleTime: 60_000,
    retry: false,
  });

  const rows = (Array.isArray(data) ? data : data?.results) ?? [];
  const shown = rows.slice(0, 3);
  const rest = rows.length - shown.length;

  return (
    <div className="hm-card hm-strip-row" role="status">
      {isLoading ? (
        <span className="hm-skel" style={{ width: '60%' }} aria-hidden="true" />
      ) : isError ? (
        <span className="hm-quiet-inline">Notices could not be loaded.</span>
      ) : rows.length === 0 ? (
        <>
          <CheckCircle2 size={18} strokeWidth={1.75} className="hm-strip-ok" aria-hidden="true" />
          <span>No unread notices</span>
        </>
      ) : (
        <>
          <Megaphone size={18} strokeWidth={1.75} className="hm-strip-ic" aria-hidden="true" />
          <span className="hm-strip-txt">
            <strong>{rows.length} unread</strong>
            {' · '}
            {shown.map((c, i) => (
              <React.Fragment key={c.id}>
                {i > 0 && ' · '}
                <Link to={`/circulars/${c.id}`} className="hm-strip-sub">
                  {c.subject || 'Untitled circular'}
                </Link>
              </React.Fragment>
            ))}
            {rest > 0 && ` · +${rest} more`}
          </span>
        </>
      )}
      <Link to={rows.length ? '/circulars/unread' : '/circulars'} className="hm-strip-link">
        All circulars →
      </Link>
    </div>
  );
};

export default UnreadNotices;
