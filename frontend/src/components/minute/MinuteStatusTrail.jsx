import React from 'react';
import { Check, Clock, Minus, Route } from 'lucide-react';

/**
 * Where the minute is in its life, as the manual's four states.
 *
 *     Draft → [Draft Review] → Acknowledgement → Archived
 *
 * The review stage is only drawn when the minute actually has an FRO, so a minute that
 * never needed one does not display a stage it will never enter. The stages come from
 * the server (`tracker`), so the strip and the status chip cannot disagree.
 *
 * @param {{ stages:Array<{key,label,state}>, acknowledgement?:Object }} props
 */
const STATE = {
  done: { tone: 'ok', Icon: Check, label: 'Done' },
  active: { tone: 'wait', Icon: Clock, label: 'Now' },
  pending: { tone: 'muted', Icon: Minus, label: '' },
};

const MinuteStatusTrail = ({ stages = [], acknowledgement }) => {
  if (!stages.length) return null;
  return (
    <div className="memo-panel">
      <h3 className="memo-panel-title">
        <Route size={15} aria-hidden="true" /> Progress
        {acknowledgement?.total > 0 && (
          <span className="min-count">
            {acknowledgement.acknowledged} of {acknowledgement.total} acknowledged
          </span>
        )}
      </h3>
      <ol className="min-trail">
        {stages.map((stage) => {
          const state = STATE[stage.state] || STATE.pending;
          const { Icon } = state;
          return (
            <li key={stage.key} className={`min-trail-step is-${stage.state}`}>
              <span className={`min-appr-node is-${state.tone}`} aria-hidden="true">
                <Icon size={13} />
              </span>
              <span className="min-trail-label">{stage.label}</span>
              {state.label && (
                <span className={`min-status is-${state.tone}`}>{state.label}</span>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
};

export default MinuteStatusTrail;
