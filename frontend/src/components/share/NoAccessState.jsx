import React from 'react';
import { useNavigate } from 'react-router-dom';
import { Lock } from 'lucide-react';

/**
 * "You cannot open this" — the page a shared link lands on when the recipient
 * is not allowed to see the record (Phase TASK-DEEP-LINK-SHARING).
 *
 * WHY THIS NEEDED A STATE OF ITS OWN
 * ----------------------------------
 * The generic error state rendered the API's own words — "No Task matches the
 * given query." — beside a Retry button that can never succeed. That was
 * tolerable while the only way to reach a task you cannot see was to guess its
 * UUID. Deep links make it an ordinary event: somebody pastes a link to a
 * colleague who is not on the task, and the first thing that colleague sees is
 * a database sentence and a button that lies to them.
 *
 * THE MESSAGE DOES NOT SAY WHETHER THE RECORD EXISTS
 * --------------------------------------------------
 * One wording covers "there is no such task" and "there is, and it is not
 * yours", because distinguishing them would turn this page into an oracle: an
 * employee could walk the reference sequence and learn exactly how much work
 * exists in a department they have no access to. The server already refuses
 * both the same way — a scoped queryset, so an invisible task is simply not
 * found — and this is that same answer, said in a sentence.
 *
 * Reusable: `kind` and `backTo` are the only things a memo, minute, circular,
 * leave application, asset or appraisal would change.
 */
const NoAccessState = ({ kind = 'task', reference, backTo = '/tasks', backLabel = 'Go to my tasks' }) => {
  const navigate = useNavigate();
  return (
    <div className="share-noaccess" role="alert">
      <span className="share-noaccess-icon" aria-hidden="true"><Lock size={26} /></span>
      <h1 className="share-noaccess-title">This {kind} is not available to you</h1>
      <p className="share-noaccess-body">
        {reference && <><b>{reference}</b>{' — '}</>}
        it either does not exist, or you are not one of the people it was shared
        with. Ask whoever sent you the link to add you to it.
      </p>
      <button type="button" className="lr-btn lr-btn-primary"
        onClick={() => navigate(backTo)}>
        {backLabel}
      </button>
    </div>
  );
};

export default NoAccessState;
