import React from 'react';

/**
 * The page header (Phase D / blueprint §21).
 *
 * Replaces the hand-built `.pg-head` block repeated across pages, each with its
 * own spacing and its own decision about whether the breadcrumb is a div or a
 * span. One component means one answer.
 *
 * `title` is an <h1> because a page has one. Several existing pages open with an
 * <h2> under no <h1>, which leaves a screen reader's heading outline starting at
 * level two - fixed here for everything that adopts it.
 */
const PageHeader = ({ breadcrumb, title, description, actions, children }) => (
  <header className="ui-head">
    <div className="ui-head-main">
      {breadcrumb && <p className="ui-head-crumb">{breadcrumb}</p>}
      <h1 className="ui-head-title">{title}</h1>
      {description && <p className="ui-head-desc">{description}</p>}
      {children}
    </div>
    {actions && <div className="ui-head-actions">{actions}</div>}
  </header>
);

export default PageHeader;
