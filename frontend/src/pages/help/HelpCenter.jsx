import React, { useMemo, useState } from 'react';
import { Link, useLocation, useSearchParams } from 'react-router-dom';
import { Search, LifeBuoy, Rocket, BookOpen, HelpCircle, Compass } from 'lucide-react';

import { useAuth } from '../../hooks/useAuth';
import { CATEGORIES, categoryOf, searchHelp, TYPE_LABEL } from '../../services/helpContent';
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
  const notice = useLocation().state?.notice;
  const [query, setQuery] = useState(params.get('q') || '');
  const [type, setType] = useState('');
  const [category, setCategory] = useState(params.get('category') || '');

  const all = useMemo(() => searchHelp(query, role), [query, role]);
  const results = useMemo(
    () => all.filter((a) => (!type || a.type === type)
      && (!category || categoryOf(a.slug) === category)),
    [all, type, category],
  );
  // Only categories this person has articles in -- an empty "Billing" chip
  // for an employee who can't see billing is a dead end.
  const present = useMemo(() => new Set(searchHelp('', role).map((a) => categoryOf(a.slug))), [role]);

  return (
    <div className="page hc">
      {notice && <p className="sp-ok" role="status">{notice}</p>}
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
          <Link className="hc-chip" to="/help/contact"><LifeBuoy size={15} aria-hidden="true" /> Create a ticket</Link>
        </div>
      </header>

      <div className="hc-cats" role="group" aria-label="Category">
        <button type="button" className={`hc-chip${!category ? ' is-on' : ''}`} aria-pressed={!category}
                onClick={() => setCategory('')}>All topics</button>
        {CATEGORIES.filter((c) => present.has(c.key)).map((c) => (
          <button key={c.key} type="button" className={`hc-chip${category === c.key ? ' is-on' : ''}`}
                  aria-pressed={category === c.key} onClick={() => setCategory(c.key)}>
            {c.label}
          </button>
        ))}
      </div>

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
