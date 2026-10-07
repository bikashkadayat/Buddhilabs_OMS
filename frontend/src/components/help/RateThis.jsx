import React, { useState } from 'react';
import { Star } from 'lucide-react';

import { supportService } from '../../services/supportService';

/**
 * "How was your experience?" / "Was this helpful?" -- one small, optional
 * question, sent to the platform team's inbox as feedback.
 *
 * Asked once per thing per person (remembered in this browser), so it never
 * nags; a comment box opens only after a low rating, where the "why" is
 * worth the most.
 */
const seenKey = (feature) => `feedback:${feature}`;

const RateThis = ({ feature, question = 'How was your experience?', onDone }) => {
  const [rating, setRating] = useState(0);
  const [hover, setHover] = useState(0);
  const [comment, setComment] = useState('');
  const [state, setState] = useState(() => {
    try { return localStorage.getItem(seenKey(feature)) ? 'done' : 'ask'; } catch { return 'ask'; }
  });

  const send = async (value, text = '') => {
    setState('sending');
    try {
      await supportService.send({ kind: 'feedback', rating: value, feature, message: text });
      try { localStorage.setItem(seenKey(feature), String(Date.now())); } catch { /* fine */ }
      setState('thanks');
      onDone?.();
    } catch {
      setState('ask');
    }
  };

  if (state === 'done') return null;
  if (state === 'thanks') {
    return <p className="rt rt-thanks" role="status">Thanks — that helps us decide what to improve.</p>;
  }

  return (
    <div className="rt" role="group" aria-label={question}>
      <span className="rt-q">{question}</span>
      <span className="rt-stars" onMouseLeave={() => setHover(0)}>
        {[1, 2, 3, 4, 5].map((n) => (
          <button key={n} type="button" className={`rt-star${(hover || rating) >= n ? ' is-on' : ''}`}
                  aria-label={`${n} out of 5`} aria-pressed={rating === n}
                  onMouseEnter={() => setHover(n)}
                  onClick={() => { setRating(n); if (n >= 4) send(n); }}
                  disabled={state === 'sending'}>
            <Star size={18} aria-hidden="true" />
          </button>
        ))}
      </span>
      {rating > 0 && rating < 4 && (
        <form className="rt-more" onSubmit={(e) => { e.preventDefault(); send(rating, comment); }}>
          <label htmlFor={`rt-${feature}`}>What would have made it better? (optional)</label>
          <textarea id={`rt-${feature}`} rows={2} value={comment} onChange={(e) => setComment(e.target.value)} />
          <button type="submit" className="btn btn-primary btn-sm" disabled={state === 'sending'}>Send</button>
        </form>
      )}
    </div>
  );
};

export default RateThis;
