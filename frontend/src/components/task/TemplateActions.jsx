import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { BookmarkPlus, LayoutTemplate } from 'lucide-react';

import { taskService } from '../../services/taskService';

/**
 * Save-as-template and apply-template, on the task detail page (Phase T2.9).
 *
 * Applying is offered only when the task has NO checklist, mirroring the
 * server's refusal: merging two lists silently produces duplicates and replacing
 * one silently discards ticks, so the engine refuses and the UI does not offer
 * an action that would only produce an error message.
 */
const TemplateActions = ({
  canApply, canSave, hasChecklist, templateName, busy, onApply, onSave,
}) => {
  const [applying, setApplying] = useState(false);
  const [saving, setSaving] = useState(false);
  const [chosen, setChosen] = useState('');
  const [name, setName] = useState('');
  const [dueInDays, setDueInDays] = useState('');

  const { data: templates } = useQuery({
    queryKey: ['task-templates'],
    queryFn: () => taskService.getTemplates(),
    enabled: applying,
    staleTime: 5 * 60 * 1000,
  });
  const rows = templates?.results ?? templates ?? [];

  if (!canApply && !canSave) return null;

  return (
    <div className="task-template-actions">
      {templateName && (
        <span className="task-sub">
          <LayoutTemplate size={12} aria-hidden="true" /> From the
          {' '}<b>{templateName}</b> template
        </span>
      )}

      <div className="task-form-actions" style={{ justifyContent: 'flex-start' }}>
        {canApply && !hasChecklist && (
          <button type="button" className="lr-btn" onClick={() => setApplying(!applying)}>
            <LayoutTemplate size={14} /> Use a template
          </button>
        )}
        {canSave && (
          <button type="button" className="lr-btn" onClick={() => setSaving(!saving)}>
            <BookmarkPlus size={14} /> Save as template
          </button>
        )}
      </div>

      {applying && (
        <div className="task-form-row">
          <label className="lr-field" style={{ flex: 2 }}>
            <span className="sr-only">Template</span>
            <select value={chosen} aria-label="Template"
              onChange={(e) => setChosen(e.target.value)}>
              <option value="">Choose a template…</option>
              {rows.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.name} ({row.item_count} items)
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="lr-btn lr-btn-primary"
            disabled={busy || !chosen}
            onClick={() => { onApply(chosen); setApplying(false); }}>
            Apply
          </button>
        </div>
      )}

      {saving && (
        <div className="task-form-row">
          <label className="lr-field" style={{ flex: 2 }}>
            <span className="sr-only">Template name</span>
            <input value={name} placeholder="Template name" aria-label="Template name"
              onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="lr-field" style={{ flex: 1 }}>
            <span className="sr-only">Due in days</span>
            <input type="number" min="0" value={dueInDays} aria-label="Due in days"
              placeholder="Due in (days)"
              onChange={(e) => setDueInDays(e.target.value)} />
          </label>
          <button type="button" className="lr-btn lr-btn-primary"
            disabled={busy || name.trim().length < 3}
            onClick={() => {
              onSave({
                name: name.trim(),
                ...(dueInDays === '' ? {}
                  : { default_due_in_days: Number(dueInDays) }),
              });
              setName('');
              setDueInDays('');
              setSaving(false);
            }}>
            Save
          </button>
        </div>
      )}
    </div>
  );
};

export default TemplateActions;
