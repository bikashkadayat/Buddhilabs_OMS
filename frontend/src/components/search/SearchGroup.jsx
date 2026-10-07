import React from 'react';

/**
 * One labelled group of results (Phase 204).
 *
 * Results carry ACTIONS, not just a destination — the brief's point that a
 * search hit should let you do the thing rather than only find it. The action
 * set comes from the Work Queue, so an Approve appears only where the server
 * already said this person may approve; search never infers permission.
 *
 * Options are `<li role="option">` rather than buttons: inside a listbox the
 * input keeps focus and `aria-activedescendant` does the pointing, so a nested
 * focusable would fight it. The secondary action IS a button, because it is
 * reachable only by pointer and needs its own accessible name.
 */
const SearchGroup = ({ group, items, flat, active, busyId, optionId, onHover, onRun }) => (
  <li className="cp-group" role="group" aria-label={group}>
    <p className="cp-group-hd">{group}</p>
    <ul className="cp-group-list">
      {items.map((item) => {
        const index = flat.indexOf(item);
        const isActive = index === active;
        const [primary, ...rest] = item.actions || [];
        return (
          <li
            key={item.id}
            id={optionId(index)}
            role="option"
            aria-selected={isActive}
            className={`cp-opt${isActive ? ' is-active' : ''}${busyId === item.id ? ' is-busy' : ''}`}
            onMouseMove={() => onHover(index)}
            onClick={() => onRun(item, primary)}
          >
            <span className="cp-opt-main">
              <span className="cp-opt-title">{item.title}</span>
              {item.subtitle && <span className="cp-opt-sub">{item.subtitle}</span>}
            </span>
            <span className="cp-opt-actions">
              {rest.map((a) => (
                <button
                  key={a.label}
                  type="button"
                  className="cp-act"
                  // The option's click would otherwise fire the primary action
                  // as well, navigating away from the thing just acted on.
                  onClick={(e) => { e.stopPropagation(); onRun(item, a); }}
                  disabled={busyId === item.id}
                  aria-label={`${a.label}: ${item.title}`}
                >
                  {a.label}
                </button>
              ))}
              {primary && <span className="cp-opt-primary" aria-hidden="true">{primary.label}</span>}
            </span>
          </li>
        );
      })}
    </ul>
  </li>
);

export default SearchGroup;
