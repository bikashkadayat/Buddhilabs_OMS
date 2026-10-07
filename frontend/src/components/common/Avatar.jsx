import React, { useState } from 'react';

/**
 * Avatar: shows the user's profile photo when present, otherwise falls back to
 * their coloured initials. A broken/failed image also falls back to initials.
 *
 * THE FALLBACK IS PER-URL, NOT PERMANENT.
 *
 * `broken` used to be set once and never cleared, so an instance that had ever
 * failed showed initials for the rest of its life — even after a perfectly
 * good `photo` arrived. That turned a transient failure into a permanent one:
 * the login response briefly carried an unsigned /media/ path that 404s, and
 * although the signed URL followed a moment later, whichever avatar had
 * already mounted stayed on initials while a newly-mounted one showed the
 * photo. Same person, two identities, decided by mount order.
 *
 * The signed URLs also EXPIRE (300s), so a long-open tab will legitimately see
 * a 403 on re-fetch. Resetting on the prop means the next refresh recovers,
 * rather than the avatar degrading for the rest of the session.
 *
 * So what is stored is WHICH URL failed, and "broken" is derived from it. A new
 * `photo` is automatically not the failed one - no effect has to notice the
 * prop changed and reset a flag, which used to cost an extra render per change.
 */
const Avatar = ({ photo, initials = 'U', color = '#6B7280', size = 40, radius = '50%', fontSize, className = '', style = {} }) => {
  const [failedSrc, setFailedSrc] = useState(null);
  // A new URL deserves a new attempt.
  const broken = failedSrc !== null && failedSrc === photo;
  const base = {
    width: size,
    height: size,
    borderRadius: radius,
    flexShrink: 0,
    ...style,
  };

  if (photo && !broken) {
    return (
      <img
        src={photo}
        alt={initials}
        className={className}
        onError={() => setFailedSrc(photo)}
        style={{ ...base, objectFit: 'cover', display: 'block' }}
      />
    );
  }

  return (
    <div
      className={className}
      style={{
        ...base,
        background: color,
        color: '#fff',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontWeight: 700,
        fontSize: fontSize || Math.round(size * 0.4),
      }}
    >
      {initials}
    </div>
  );
};

export default Avatar;
