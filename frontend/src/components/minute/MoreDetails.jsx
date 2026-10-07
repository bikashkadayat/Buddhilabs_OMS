import React from 'react';
import { ChevronDown } from 'lucide-react';

/**
 * A closed-by-default disclosure for the parts of the minute module that are real but
 * rarely wanted: the full audit trail, the traceability map, optional header fields.
 *
 * Nothing is deleted by folding it away — the page simply stops opening with everything
 * at once, which is what made it hard to read. `<details>` is used rather than a state
 * toggle so it works without JavaScript state, is keyboard operable by default, and
 * lands in the accessibility tree as a real disclosure.
 *
 * @param {{ title:string, hint?:string, children:React.ReactNode }} props
 */
const MoreDetails = ({ title, hint, children }) => (
  <details className="min-more">
    <summary className="min-more-summary">
      <ChevronDown size={15} aria-hidden="true" className="min-more-chevron" />
      <span className="min-more-title">{title}</span>
      {hint && <span className="min-more-hint">{hint}</span>}
    </summary>
    <div className="min-more-body">{children}</div>
  </details>
);

export default MoreDetails;
