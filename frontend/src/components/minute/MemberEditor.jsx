import React from 'react';
import { UserCheck, UserMinus, UserPlus, Users, X } from 'lucide-react';

import EmployeeSelector from '../memo/EmployeeSelector';

/**
 * The manual's three member pickers (E-minute-manual p.4):
 *
 *     Members Present   Members Absent   Invitee Members
 *
 * Three separate lists rather than one list with an attendance dropdown, because that
 * is how the form asks for it - and because the distinction is load-bearing: only the
 * members PRESENT are asked to acknowledge the minute (p.8), so which box a person is
 * in decides whether the minute waits for them.
 *
 * The value handed back is one flat array of { user_id, attendance, … }, which is what
 * the participants endpoint takes.
 *
 * @param {{ rows:Array<Object>, onChange:(rows:Array<Object>)=>void,
 *           readOnly?:boolean, error?:string }} props
 */
const GROUPS = [
  {
    code: 'present',
    label: 'Members Present',
    hint: 'They are the people asked to acknowledge the minute once it is submitted.',
    Icon: UserCheck,
    placeholder: 'Add a member who attended…',
  },
  {
    code: 'absent',
    label: 'Members Absent',
    hint: 'Recorded on the minute and stamped ABSENT on the signature sheet. '
      + 'They are not asked to acknowledge.',
    Icon: UserMinus,
    placeholder: 'Add a member who did not attend…',
  },
  {
    code: 'invitee',
    label: 'Invitee Members',
    hint: 'Guests at the meeting. Recorded, not asked to acknowledge.',
    Icon: UserPlus,
    placeholder: 'Add an invitee…',
  },
];

/**
 * One chosen person. Defined at module level rather than inside the panel: a component
 * created during render is a new type on every keystroke, which remounts the row and
 * loses focus.
 */
const Person = ({ row, onRemove, readOnly }) => (
  <div className="min-person">
    <span className="min-person-main">
      <span className="min-person-name">{row.full_name}</span>
      {row.designation && <span className="min-person-sub">{row.designation}</span>}
    </span>
    {!readOnly && (
      <button type="button" className="memo-icon-btn is-danger"
        aria-label={`Remove ${row.full_name}`} onClick={onRemove}>
        <X size={14} />
      </button>
    )}
  </div>
);

const MemberEditor = ({ rows = [], onChange, readOnly = false, error }) => {
  const taken = rows.map((row) => row.user_id);

  const add = (employee, attendance) => onChange([...rows, {
    user_id: String(employee.id),
    full_name: employee.full_name,
    designation: employee.designation || '',
    department: employee.department || '',
    attendance,
  }]);

  const remove = (userId) => onChange(rows.filter((row) => row.user_id !== userId));

  const presentCount = rows.filter((row) => row.attendance === 'present').length;

  return (
    <section aria-labelledby="members-heading">
      <h3 className="memo-panel-title" id="members-heading">
        <Users size={15} aria-hidden="true" /> Members
        <span className="min-count">{rows.length}</span>
      </h3>
      <p className="lr-page-sub">
        Who was at the meeting. When the minute is submitted it goes to the
        {' '}<b>{presentCount}</b> member{presentCount === 1 ? '' : 's'} recorded as
        present, and archives itself once they have all confirmed reading it.
      </p>

      {error && <p className="memo-notice is-warn" role="alert">{error}</p>}

      {GROUPS.map(({ code, label, hint, Icon, placeholder }) => {
        const group = rows.filter((row) => row.attendance === code);
        return (
          <div className="min-approval-block" key={code}>
            <div className="min-approval-label">
              <Icon size={14} aria-hidden="true" /> {label}
              {code === 'present' && <span className="min-req">required</span>}
              <span className="min-count">{group.length}</span>
            </div>
            <p className="min-approval-help">{hint}</p>
            {group.map((row) => (
              <Person key={row.user_id} row={row} readOnly={readOnly}
                onRemove={() => remove(row.user_id)} />
            ))}
            {!readOnly && (
              <EmployeeSelector onSelect={(user) => add(user, code)}
                exclude={taken} placeholder={placeholder} />
            )}
          </div>
        );
      })}
    </section>
  );
};

export default MemberEditor;
