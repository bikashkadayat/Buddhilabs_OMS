import React, { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Search, LifeBuoy, Rocket, BookOpen, HelpCircle, Compass } from 'lucide-react';

import { useAuth } from '../../hooks/useAuth';
import { searchHelp, TYPE_LABEL } from '../../services/helpContent';
import { restartTour } from '../../services/tours';

/**
 * Help, inside the product. Search the how-tos and common questions, take
 * the tour again, or write to us -- without leaving the workspace.
 */
const FILTERS = [
  { key: '', label: 'Everything' },
  { key: 'tutorial', label: 'How to' },
  { key: 'faq', label: 'Questions' },
  { key: 'guide', label: 'Guides' },
];

const HelpCenter = () => {
  const { role } = useAuth();
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState(params.get('q') || '');
  const [type, setType] = useState('');

  const results = useMemo(
    () => searchHelp(query, role).filter((a) => !type || a.type === type),
    [query, role, type],
  );

  return (
    <div className="page hc">
      <header className="hc-hero">
        <h1>How can we help?</h1>
        <label className="hc-search">
          <Search size={18} aria-hidden="true" />
          <input type="search" value={query} placeholder="Search help — e.g. “apply for leave”, “forgot password”"
                 aria-label="Search help" autoFocus
                 onChange={(e) => { setQuery(e.target.value); setParams(e.target.value ? { q: e.target.value } : {}, { replace: true }); }} />
        </label>
        <div className="hc-quick">
          {role === 'admin' && (
            <Link className="hc-chip" to="/getting-started"><Rocket size={15} aria-hidden="true" /> Getting started</Link>
          )}
          <button type="button" className="hc-chip" onClick={restartTour}>
            <Compass size={15} aria-hidden="true" /> Take the tour again
          </button>
          <Link className="hc-chip" to="/help/contact"><LifeBuoy size={15} aria-hidden="true" /> Contact support</Link>
        </div>
      </header>

      <div className="pf-tabs" role="tablist" aria-label="Kind of help">
        {FILTERS.map((f) => (
          <button key={f.key} type="button" role="tab" aria-selected={type === f.key}
                  className={`pf-tab${type === f.key ? ' is-active' : ''}`} onClick={() => setType(f.key)}>
            {f.label}
          </button>
        ))}
      </div>

      {results.length === 0 ? (
        <div className="hc-none">
          <HelpCircle size={28} aria-hidden="true" />
          <p>Nothing matches “{query}”.</p>
          <p className="hc-muted">Try fewer words — or ask us directly.</p>
          <Link className="btn btn-primary" to={`/help/contact?subject=${encodeURIComponent(query)}`}>Ask support</Link>
        </div>
      ) : (
        <ul className="hc-list">
          {results.map((a) => (
            <li key={a.slug}>
              <Link className="hc-item" to={`/help/${a.slug}`}>
                <span className={`hc-type is-${a.type}`}>
                  {a.type === 'tutorial' ? <BookOpen size={13} aria-hidden="true" /> : <HelpCircle size={13} aria-hidden="true" />}
                  {TYPE_LABEL[a.type]}
                </span>
                <strong>{a.title}</strong>
                <span className="hc-muted">{a.summary}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

export default HelpCenter;
