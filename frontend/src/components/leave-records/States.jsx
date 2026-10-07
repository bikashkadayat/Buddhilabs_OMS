import { describeApiError } from '../../services/apiErrors';
import React from 'react';
import { useNavigate } from 'react-router-dom';

/**
 * Loading skeleton (not a spinner) - a set of shimmering placeholder blocks.
 * @param {{rows?:number, height?:number}} props
 */
export const Skeleton = ({ rows = 3, height = 96 }) => (
  <div className="lr-skeleton" aria-busy="true" aria-live="polite" role="status">
    <span className="sr-only">Loading…</span>
    {Array.from({ length: rows }).map((_, i) => (
      <div key={i} className="lr-skel-block" style={{ height }} />
    ))}
  </div>
);

/**
 * Empty state with a call-to-action.
 * @param {{message?:string, ctaLabel?:string, ctaTo?:string}} props
 */
export const EmptyState = ({
  message = 'No leaves recorded for this year.',
  ctaLabel = 'Apply for leave',
  ctaTo = '/leave/apply',
}) => {
  const navigate = useNavigate();
  return (
    <div className="lr-empty" role="status">
      <svg viewBox="0 0 24 24" width="40" height="40" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
        <rect x="3" y="4" width="18" height="18" rx="2" /><path d="M16 2v4M8 2v4M3 10h18" />
      </svg>
      <p className="lr-empty-msg">{message}</p>
      {ctaTo && (
        <button type="button" className="lr-btn lr-btn-primary" onClick={() => navigate(ctaTo)}>
          {ctaLabel}
        </button>
      )}
    </div>
  );
};

/**
 * Error state with a retry button and a human-readable message.
 * @param {{error?:Error, onRetry?:Function}} props
 */
export const ErrorState = ({ error, onRetry }) => {
  // Through the shared describer: the server's own sentence when it wrote
  // one, never axios's "Network Error" or "Request failed with status code
  // 500", which about seventy screens could show from here.
  const message = describeApiError(error, 'We couldn’t load this right now. Please try again.');
  return (
    <div className="lr-error" role="alert">
      <svg viewBox="0 0 24 24" width="40" height="40" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
        <circle cx="12" cy="12" r="10" /><path d="M12 8v4M12 16h.01" />
      </svg>
      <p className="lr-error-msg">{message}</p>
      {onRetry && (
        <button type="button" className="lr-btn lr-btn-ghost" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
};
