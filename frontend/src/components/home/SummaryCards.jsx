import React from 'react';
import { Link } from 'react-router-dom';
import {
  Inbox, ClipboardCheck, CalendarClock, CheckCircle2,
} from 'lucide-react';
import { buildWorkSummary } from './workSummary';

/**
 * The action summary: four compact cards in one row, at every width.
 *
 * One component, not a desktop and a mobile one (see workSummary.js for why).
 * The row is four across on a desktop and a tablet, two by two on a phone —
 * that is a stylesheet decision, and the markup is identical.
 *
 * Every card is a Link: a number here is never purely for reading.
 */
const ICONS = {
  attention: Inbox,
  reviews: ClipboardCheck,
  due: CalendarClock,
  completed: CheckCircle2,
};

const SummaryCards = ({ counts = {}, dashboard = null, loading = false }) => {
  const cards = buildWorkSummary({ counts, dashboard });
  return (
    <div className="hm-sum" aria-label="Summary" data-tour="reviews">
      {cards.map((c) => {
        const Icon = ICONS[c.key];
        return (
          <Link key={c.key} to={c.to} className={`hm-card hm-sum-card is-${c.tone}`}>
            <span className="hm-sum-ico" aria-hidden="true"><Icon size={18} strokeWidth={1.75} /></span>
            <span className="hm-sum-body">
              {/* While the counts load: a shimmer where the number will be, and
                  the dash kept for assistive tech (and for the tests that read
                  the text) rather than a number that is not yet true. */}
              <span className="hm-sum-n">
                {loading
                  ? <><span className="hm-skel hm-skel-n" aria-hidden="true" /><span className="sr-only">—</span></>
                  : c.n}
              </span>
              <span className="hm-sum-l">{c.label}</span>
            </span>
          </Link>
        );
      })}
    </div>
  );
};

export default SummaryCards;
