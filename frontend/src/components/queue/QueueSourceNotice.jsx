import React from 'react';

/**
 * The partial-failure line (blueprint §04).
 *
 * When one of the six sources rejects, its rows are simply absent and this says
 * so in one quiet sentence. Deliberately NOT an error banner: five working
 * sources and one broken one is a working queue with a gap, and dressing that
 * as a failure would train people to distrust a page that is mostly correct.
 */
const QueueSourceNotice = ({ sources = [], onRetry }) => {
  const failed = sources.filter((s) => !s.ok);
  if (!failed.length) return null;

  const names = failed.map((s) => s.label).join(', ');
  return (
    <div className="wq-notice" role="status">
      <span>
        {names} could not be loaded, so nothing from {failed.length === 1 ? 'it' : 'them'} is shown here.
        Everything else is up to date.
      </span>
      <button type="button" className="wq-link" onClick={onRetry}>Retry</button>
    </div>
  );
};

export default QueueSourceNotice;
