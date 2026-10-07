import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';

import { appraisalService } from '../../services/appraisalService';
import { Skeleton } from '../../components/leave-records/States';
import PersonPicker from '../../components/appraisal/PersonPicker';

/**
 * Open an appraisal (Phase APM-03b) — HR only.
 *
 * TWO RULES THE FORM MAKES VISIBLE
 * --------------------------------
 * Nobody supervises their own appraisal, and nobody sits on the committee
 * reviewing it. The server refuses both; this form refuses them too, because
 * discovering the rule from a 400 after choosing five committee members is
 * discovering it too late. The whole process rests on there being a second
 * person in it.
 *
 * WHY THE COMMITTEE IS OPTIONAL
 * -----------------------------
 * Most appraisals never reach a committee — it is the stage for the ones that
 * need a view from outside the reporting line. Requiring members up front would
 * mean naming people who will have read access to a self-assessment they may
 * never need to see.
 */
const NewAppraisal = () => {
  const navigate = useNavigate();
  const [cycle, setCycle] = useState('');
  const [employee, setEmployee] = useState(null);
  const [supervisor, setSupervisor] = useState(null);
  const [committee, setCommittee] = useState([]);
  const [error, setError] = useState('');

  const cycles = useQuery({
    queryKey: ['appraisal', 'cycles'],
    queryFn: () => appraisalService.getCycles(),
  });

  const create = useMutation({
    mutationFn: (payload) => appraisalService.createAppraisal(payload),
    onSuccess: (row) => navigate(`/appraisals/${row.id}`),
    onError: (err) => setError(
      err?.response?.data?.detail
      || Object.values(err?.response?.data || {}).flat().join(' ')
      || 'That appraisal could not be opened.',
    ),
  });

  if (cycles.isLoading) return <div className="page"><Skeleton rows={3} /></div>;

  const rows = cycles.data?.results || cycles.data || [];
  const open = rows.filter((row) => row.status !== 'closed');

  const submit = (event) => {
    event.preventDefault();
    if (!employee || !supervisor) {
      setError('Choose both an employee and a supervisor.');
      return;
    }
    if (supervisor.id === employee.id) {
      setError('Somebody cannot supervise their own appraisal.');
      return;
    }
    if (committee.some((m) => m.id === employee.id)) {
      setError('Somebody cannot sit on the committee reviewing their own '
        + 'appraisal.');
      return;
    }
    create.mutate({
      cycle, employee: employee.id, supervisor: supervisor.id,
      committee: committee.map((m) => m.id),
    });
  };

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">Open an Appraisal</h1>
          <p className="lr-page-sub">
            One appraisal per person per cycle.
          </p>
        </div>
      </div>

      {error && <div className="task-callout is-no" role="alert">{error}</div>}

      {open.length === 0 ? (
        <p className="lr-page-sub">
          There is no open cycle. Create one first.
        </p>
      ) : (
        <form className="apr-goal-form" onSubmit={submit}>
          <label className="apr-field">
            <span>Cycle</span>
            <select value={cycle} required
              onChange={(e) => setCycle(e.target.value)}>
              <option value="">Choose a cycle</option>
              {open.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.name} ({row.status_label || row.status})
                </option>
              ))}
            </select>
          </label>

          <PersonPicker id="apr-employee" label="Employee"
            value={employee?.id} valueName={employee?.full_name}
            onChange={setEmployee} />

          <PersonPicker id="apr-supervisor" label="Supervisor"
            help="The person who agrees the objectives and writes the review."
            value={supervisor?.id} valueName={supervisor?.full_name}
            onChange={setSupervisor} />

          <PersonPicker id="apr-committee" label="Review committee (optional)"
            help="Only needed when a view from outside the reporting line is wanted."
            value={committee.length}
            valueName={committee.map((m) => m.full_name).join(', ')}
            onChange={(person) => setCommittee(
              committee.some((m) => m.id === person.id)
                ? committee : [...committee, person])} />

          {committee.length > 0 && (
            <ul className="apr-people">
              {committee.map((member) => (
                <li key={member.id}>
                  {member.full_name}
                  <button type="button" className="btn btn-ghost btn-xs"
                    onClick={() => setCommittee(
                      committee.filter((m) => m.id !== member.id))}>
                    Remove {member.full_name} from the committee
                  </button>
                </li>
              ))}
            </ul>
          )}

          <div className="apr-goal-actions">
            <button type="submit" className="btn btn-primary btn-sm"
              disabled={create.isPending}>Open appraisal</button>
          </div>
        </form>
      )}
    </div>
  );
};

export default NewAppraisal;
