import React, { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { appraisalService } from '../../services/appraisalService';

/**
 * Pick a person (Phase APM-03b).
 *
 * SEARCH-GATED BY DESIGN, NOT BY ACCIDENT
 * ---------------------------------------
 * The directory behind this refuses queries under two characters, is capped and
 * throttled, and never returns an email address — so it cannot be used to walk
 * the roster. A dropdown pre-loaded with every employee would defeat all three
 * protections at once, which is why this is a search box and not a `<select>`.
 *
 * The chosen person is echoed back in text. A picker that shows only an id, or
 * clears itself after choosing, is how somebody ends up raising an appraisal
 * against the wrong colleague.
 */
const PersonPicker = ({ id, label, value, valueName, onChange, help }) => {
  const [query, setQuery] = useState('');
  const [term, setTerm] = useState('');

  // Debounced: the endpoint is throttled, and a request per keystroke would
  // spend that budget on prefixes nobody wanted.
  useEffect(() => {
    const timer = setTimeout(() => setTerm(query.trim()), 250);
    return () => clearTimeout(timer);
  }, [query]);

  const { data = [], isFetching } = useQuery({
    queryKey: ['people', term],
    queryFn: () => appraisalService.searchPeople(term),
    enabled: term.length >= 2,
    staleTime: 60_000,
  });

  return (
    <div className="apr-field">
      <label htmlFor={id}>{label}</label>
      {help && <span className="memo-tile-hint">{help}</span>}
      <input id={id} type="search" value={query} autoComplete="off"
        placeholder="Type at least two letters"
        aria-describedby={`${id}-chosen`}
        onChange={(e) => setQuery(e.target.value)} />
      <p id={`${id}-chosen`} className="memo-tile-hint" role="status">
        {value ? `Chosen: ${valueName}` : 'Nobody chosen yet.'}
      </p>
      {term.length >= 2 && (
        <ul className="apr-people">
          {isFetching && <li className="memo-tile-hint">Searching…</li>}
          {!isFetching && data.length === 0 && (
            <li className="memo-tile-hint">Nobody matched “{term}”.</li>
          )}
          {data.map((person) => (
            <li key={person.id}>
              <button type="button" className="btn btn-ghost btn-xs"
                onClick={() => {
                  onChange(person);
                  setQuery('');
                }}>
                {person.full_name}
                {person.designation ? ` · ${person.designation}` : ''}
                {person.department ? ` · ${person.department}` : ''}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

export default PersonPicker;
