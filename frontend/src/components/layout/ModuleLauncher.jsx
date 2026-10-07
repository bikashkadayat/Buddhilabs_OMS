import React from 'react';
import { Link, useNavigate } from 'react-router-dom';

/**
 * The launcher grid (Phase E / blueprint §05).
 *
 * Each card takes an `icon`: a Lucide component, the one icon family this
 * project uses. Passing the component rather than a name keeps the set
 * tree-shakeable and makes a typo a build error instead of a blank tile.
 *
 * One component; the four launcher pages are configuration. Each card carries
 * live counts and one named action, so the choice of where to go is made on
 * evidence rather than on remembering what a module is called.
 *
 * The card is a link and the action is a nested button, which cannot be nested
 * in HTML - so the card is a Link and the action stops propagation. Rendering
 * the card as a div with an onClick would lose keyboard access and the browser's
 * own "open in new tab", which people use constantly on a launcher.
 */

const Card = ({ card }) => {
  const navigate = useNavigate();
  const counts = (card.counts || []).filter(([, v]) => v !== undefined);

  return (
    <Link
      to={card.to}
      className={`ml-card${card.disabled ? ' is-disabled' : ''}`}
      aria-disabled={card.disabled || undefined}
      onClick={(e) => { if (card.disabled) e.preventDefault(); }}
    >
      <span className="ml-ico" style={{ background: card.accent }} aria-hidden="true">
        {/* An ICON, not an initial (Phase OMS-ICON-STANDARDIZATION).
            These tiles carried one or two letters — M, Mi, C, D, Ap — which
            read as placeholder text rather than as a product: "Mi" tells you
            nothing that the word "Minute" beside it does not already say, and
            two modules starting with the same letter get arbitrary
            disambiguation. Size and stroke are fixed here rather than at each
            call site so twenty-four tiles cannot drift apart. */}
        {card.icon ? <card.icon size={20} strokeWidth={2} aria-hidden="true" /> : null}
      </span>
      <span className="ml-name">{card.title}</span>

      {counts.length > 0 ? (
        <span className="ml-counts">
          {counts.map(([label, value]) => (
            <span key={label} className="ml-count">
              <b>{value ?? '—'}</b> {label}
            </span>
          ))}
        </span>
      ) : (
        <span className="ml-counts ml-counts-empty">{card.blurb || ''}</span>
      )}

      {card.action && !card.disabled && (
        <button
          type="button"
          className="ml-act"
          onClick={(e) => { e.preventDefault(); e.stopPropagation(); navigate(card.action.to); }}
        >
          {card.action.label}
        </button>
      )}
      {card.disabled && <span className="ml-soon">Coming soon</span>}
    </Link>
  );
};

const ModuleLauncher = ({ title, description, cards, children }) => (
  <div className="page ml-page">
    <div className="pg-head">
      <div className="pg-head-left">
        <div className="pg-breadcrumb">Workspace</div>
        <h1 className="pg-title">{title}</h1>
        {description && <div className="pg-desc">{description}</div>}
      </div>
    </div>

    <div className="ml-grid">
      {cards.map((c) => <Card key={c.key} card={c} />)}
    </div>

    {children}
  </div>
);

export default ModuleLauncher;
