import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Search } from 'lucide-react';

import { taskService } from '../../services/taskService';

/**
 * Search for a person and pick them (Phase TASK-CREATE-UI-POLISH).
 *
 * ONE PICKER PER CARD, NOT ONE PICKER WITH A MODE
 * -----------------------------------------------
 * The form had a single search box beside a dropdown reading "Add as assignee /
 * Set as reviewer", so choosing a reviewer meant changing a mode first and the
 * same box did two unrelated jobs. Worse, the mode was sticky: a person who set
 * a reviewer and then searched for a second assignee replaced their reviewer
 * instead. Each card now owns its own picker, and the question it answers is
 * the question the card is asking.
 *
 * The search state lives HERE rather than in the form, which is what makes two
 * of them on one page independent.
 */
const PersonPicker = ({ label, placeholder, excludeIds = [], onPick }) => {
  const [search, setSearch] = useState('');
  const term = search.trim();

  const { data: results = [], isFetching } = useQuery({
    queryKey: ['tasks', 'employees', term],
    queryFn: () => taskService.searchEmployees(term),
    enabled: term.length >= 2,
    staleTime: 60_000,
  });

  const choices = results.filter((person) => !excludeIds.includes(person.id));

  return (
    <div className="task-picker-wrap">
      <label className="lr-field">
        <span className="sr-only">{label}</span>
        <input value={search} aria-label={label} placeholder={placeholder}
          onChange={(e) => setSearch(e.target.value)} />
      </label>

      {term.length >= 2 && (
        <ul className="task-picker" aria-label={`${label} results`}>
          {isFetching && choices.length === 0 && (
            <li className="task-sub">Searching…</li>
          )}
          {!isFetching && choices.length === 0 && (
            <li className="task-sub">
              <Search size={12} aria-hidden="true" /> No employee matches that.
            </li>
          )}
          {choices.map((person) => (
            <li key={person.id}>
              <button type="button"
                onClick={() => { onPick(person); setSearch(''); }}>
                <b>{person.full_name}</b>
                <span className="task-sub">
                  {[person.designation, person.department]
                    .filter(Boolean).join(' · ') || '—'}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

export default PersonPicker;
