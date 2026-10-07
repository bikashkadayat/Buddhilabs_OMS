import React from 'react';
import SearchGroup from './SearchGroup';

/**
 * The listbox (Phase 204).
 *
 * One `role="listbox"` spanning every group, with the groups as
 * `role="group"` inside it. Splitting into several listboxes would be easier to
 * write and wrong: arrow keys traverse ONE sequence across all groups, and
 * `aria-activedescendant` can only point inside a single listbox.
 *
 * `flat` carries the same rows in the same order the keyboard walks, so the
 * index a group renders and the index the palette highlights cannot drift.
 */
const SearchResults = ({
  groups, flat, active, busyId, listboxId, optionId, onHover, onRun, emptyLabel,
}) => {
  if (!groups.length) {
    return emptyLabel
      ? <p className="cp-empty">{emptyLabel}</p>
      : <p className="cp-empty cp-empty-idle">Search people, tasks, leave, documents, pages and help — type at least two letters.</p>;
  }

  return (
    <ul className="cp-list" role="listbox" id={listboxId} aria-label="Search results">
      {groups.map((g) => (
        <SearchGroup
          key={g.group}
          group={g.group}
          items={g.items}
          flat={flat}
          active={active}
          busyId={busyId}
          optionId={optionId}
          onHover={onHover}
          onRun={onRun}
        />
      ))}
    </ul>
  );
};

export default SearchResults;
