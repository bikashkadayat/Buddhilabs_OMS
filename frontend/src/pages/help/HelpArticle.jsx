import React from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, LifeBuoy } from 'lucide-react';

import { articleBySlug, TYPE_LABEL, helpFor } from '../../services/helpContent';
import { useAuth } from '../../hooks/useAuth';
import RateThis from '../../components/help/RateThis';

const HelpArticle = () => {
  const { slug } = useParams();
  const { role } = useAuth();
  const article = articleBySlug(slug);

  if (!article) {
    return (
      <div className="page hc">
        <p>That help page doesn’t exist. <Link to="/help">Search help</Link>.</p>
      </div>
    );
  }
  const related = helpFor(role)
    .filter((a) => a.slug !== slug && a.keywords.split(' ').some((k) => article.keywords.includes(k)))
    .slice(0, 3);

  return (
    <div className="page hc">
      <Link className="hc-back" to="/help"><ArrowLeft size={15} aria-hidden="true" /> Help</Link>
      <article className="hc-article">
        <span className={`hc-type is-${article.type}`}>{TYPE_LABEL[article.type]}</span>
        <h1>{article.title}</h1>
        <p className="hc-lede">{article.summary}</p>
        {article.steps.length > 1 ? (
          <ol className="hc-steps">{article.steps.map((s) => <li key={s}>{s}</li>)}</ol>
        ) : (
          <p className="hc-body">{article.steps[0]}</p>
        )}
        {article.links.length > 0 && (
          <div className="hc-links">
            {article.links.map((l) => <Link key={l.to} className="btn btn-primary" to={l.to}>{l.label}</Link>)}
          </div>
        )}
        <RateThis feature={`help:${article.slug}`} question="Was this helpful?" />
      </article>
      {related.length > 0 && (
        <aside className="hc-related">
          <h2>Related</h2>
          <ul>{related.map((a) => <li key={a.slug}><Link to={`/help/${a.slug}`}>{a.title}</Link></li>)}</ul>
        </aside>
      )}
      <p className="hc-muted hc-still">
        Still stuck? <Link to={`/help/contact?subject=${encodeURIComponent(article.title)}`}><LifeBuoy size={14} aria-hidden="true" /> Ask support</Link>
      </p>
    </div>
  );
};

export default HelpArticle;
