import React from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';

/**
 * DRF-envelope pagination. Driven by `count` + the page size the API uses, so
 * it stays correct when a filter narrows the result set.
 */
const Pagination = ({ page = 1, count = 0, pageSize = 20, onChange }) => {
  const pages = Math.max(1, Math.ceil(count / pageSize));
  if (count === 0) return null;

  const from = (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, count);

  return (
    <nav className="wf-pagination" aria-label="Pagination">
      <span className="wf-pagination-info">
        {from}–{to} of {count}
      </span>
      <div className="wf-pagination-controls">
        <button
          type="button" className="wf-btn wf-btn-ghost wf-btn-icon"
          onClick={() => onChange(page - 1)} disabled={page <= 1}
          aria-label="Previous page"
        >
          <ChevronLeft size={16} />
        </button>
        <span className="wf-pagination-page">Page {page} of {pages}</span>
        <button
          type="button" className="wf-btn wf-btn-ghost wf-btn-icon"
          onClick={() => onChange(page + 1)} disabled={page >= pages}
          aria-label="Next page"
        >
          <ChevronRight size={16} />
        </button>
      </div>
    </nav>
  );
};

export default Pagination;
