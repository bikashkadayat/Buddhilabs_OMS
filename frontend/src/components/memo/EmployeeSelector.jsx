import React, { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Search, X } from 'lucide-react';
import { memoService } from '../../services/memoService';

const MIN_QUERY = 2; // the server returns [] below this (roster-enumeration guard)

/**
 * Searchable employee picker for the approval matrix (Phase 4).
 *
 * Deliberately searches ALL employees rather than a role-filtered list: the spec
 * requires that any employee can be placed in any role type. Results come from
 * the server per keystroke instead of loading the roster up front, so the
 * directory still cannot be dumped.
 *
 * @param {{ onSelect:(employee:Object)=>void, exclude?:string[],
 *           placeholder?:string, autoFocus?:boolean }} props
 *   exclude: ids already in the matrix — shown greyed out rather than hidden, so
 *   a user searching for someone they already added gets an explanation instead
 *   of an empty result they cannot account for.
 */
const EmployeeSelector = ({ onSelect, exclude = [], placeholder = 'Search employee by name, ID or designation…', autoFocus = false }) => {
  const [search, setSearch] = useState('');
  const [open, setOpen] = useState(false);
  const boxRef = useRef(null);
  const query = search.trim();
  const enabled = query.length >= MIN_QUERY;

  const { data: employees = [], isLoading } = useQuery({
    queryKey: ['memo-employees', query],
    queryFn: () => memoService.searchEmployees(query),
    enabled,
    staleTime: 30_000,
  });

  // Close on an outside click so the menu never covers the matrix table.
  useEffect(() => {
    if (!open) return undefined;
    const onDocClick = (e) => { if (!boxRef.current?.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [open]);

  const excluded = new Set(exclude.map(String));

  const pick = (employee) => {
    if (excluded.has(String(employee.id))) return;
    onSelect(employee);
    setSearch('');
    setOpen(false);
  };

  return (
    <div className="memo-empsel" ref={boxRef}>
      <div className="memo-empsel-input">
        <Search size={14} aria-hidden="true" />
        <input
          type="search"
          value={search}
          autoFocus={autoFocus}
          placeholder={placeholder}
          aria-label="Search employee"
          onChange={(e) => { setSearch(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
        />
        {search && (
          <button type="button" className="memo-empsel-clear" aria-label="Clear search"
            onClick={() => { setSearch(''); setOpen(false); }}>
            <X size={13} />
          </button>
        )}
      </div>

      {open && (
        <div className="memo-empsel-menu" role="listbox">
          {!enabled && (
            <div className="memo-empsel-hint">Type at least {MIN_QUERY} characters to search.</div>
          )}
          {enabled && isLoading && <div className="memo-empsel-hint">Searching…</div>}
          {enabled && !isLoading && employees.length === 0 && (
            <div className="memo-empsel-hint">No matching employee.</div>
          )}
          {employees.map((employee) => {
            const already = excluded.has(String(employee.id));
            return (
              <button
                key={employee.id}
                type="button"
                role="option"
                aria-selected={false}
                aria-disabled={already}
                disabled={already}
                className={`memo-empsel-opt ${already ? 'is-used' : ''}`}
                onClick={() => pick(employee)}
              >
                <span className="memo-empsel-avatar" aria-hidden="true">
                  {(employee.full_name || '?').split(' ').map((w) => w[0] || '').join('').slice(0, 2).toUpperCase()}
                </span>
                <span className="memo-empsel-body">
                  <span className="memo-empsel-name">{employee.full_name}</span>
                  <span className="memo-empsel-sub">
                    {[employee.designation, employee.department, employee.employee_id]
                      .filter(Boolean).join(' · ') || employee.role_display}
                  </span>
                </span>
                {already && <span className="memo-empsel-tag">Already added</span>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default EmployeeSelector;
