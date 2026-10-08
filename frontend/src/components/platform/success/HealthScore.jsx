import React from 'react';
import { BAND_TONE } from './format';

/** The 0-100 score with its band, and -- on demand -- the six components behind it. */
const HealthScore = ({ org, detailed = false }) => (
  <div className="cs-score-wrap">
    <div className={`ch-score is-${BAND_TONE[org.band] || 'warn'}`} aria-label={`Health ${org.health} of 100, ${org.band_label}`}>
      <b>{org.health}</b><small>{org.band_label}</small>
    </div>
    {detailed && (
      <ul className="cs-components" aria-label="What the score is made of">
        {org.components.map((c) => (
          <li key={c.key} title={`${c.label}: ${c.score}/100 (weight ${c.weight}%)`}>
            <span>{c.label}</span>
            <span className="cs-bar" aria-hidden="true"><i style={{ width: `${c.score}%` }} /></span>
            <b>{c.score}</b>
          </li>
        ))}
      </ul>
    )}
  </div>
);

export default HealthScore;
