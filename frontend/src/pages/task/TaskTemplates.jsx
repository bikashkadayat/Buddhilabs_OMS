import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Archive, LayoutTemplate, Plus, X } from 'lucide-react';

import { useAuth } from '../../hooks/useAuth';
import { taskService } from '../../services/taskService';
import { can } from '../../services/roles';
import { TASK_PRIORITIES, priorityTone } from '../../components/task/taskLabels';
import { Skeleton, ErrorState } from '../../components/leave-records/States';

/**
 * The template catalogue (Phase T2.9).
 *
 * READ IS OPEN, WRITE IS NOT — an employee cannot create a task, but they can be
 * shown what a "Monthly Report" involves, and hiding the catalogue from them
 * buys nothing. The editor is gated to the roles that may hand out work, which
 * mirrors tasks.permissions.can_manage_templates; the server is what enforces it.
 *
 * Retiring, not deleting: a hard delete would strip the name off every task
 * raised from the template, and "where did this checklist come from" is exactly
 * what somebody asks two years later.
 */

const Editor = ({ onCancel, onSave, saving, error }) => {
  const [form, setForm] = useState({
    name: '', description: '', priority: 'medium', default_due_in_days: '',
  });
  const [items, setItems] = useState([]);
  const [sections, setSections] = useState([]);
  const [draft, setDraft] = useState('');

  const set = (field) => (e) => setForm({ ...form, [field]: e.target.value });
  const fieldError = (name) => (Array.isArray(error?.[name])
    ? error[name][0] : error?.[name]);

  return (
    <form className="task-form" onSubmit={(e) => {
      e.preventDefault();
      onSave({
        name: form.name.trim(),
        description: form.description,
        priority: form.priority,
        default_due_in_days: form.default_due_in_days === ''
          ? null : Number(form.default_due_in_days),
        items: items.map((t) => t.trim()).filter(Boolean),
        groups: sections
          .map((s) => ({
            title: s.title.trim() || 'Section',
            items: s.items.map((t) => t.trim()).filter(Boolean),
          }))
          .filter((s) => s.items.length > 0),
      });
    }}>
      <label className="lr-field">
        <span>Template Name *</span>
        <input value={form.name} onChange={set('name')} required
          placeholder="Monthly Report" />
        {fieldError('name') && (
          <em className="task-field-error">{fieldError('name')}</em>
        )}
      </label>

      <label className="lr-field">
        <span>Description</span>
        <textarea value={form.description} onChange={set('description')} rows={3}
          placeholder="What this template is for, and when to use it." />
      </label>

      <div className="task-form-row">
        <label className="lr-field">
          <span>Default Priority</span>
          <select value={form.priority} onChange={set('priority')}>
            {TASK_PRIORITIES.map((p) => (
              <option key={p.value} value={p.value}>{p.label}</option>
            ))}
          </select>
        </label>
        <label className="lr-field">
          <span>Due In (days)</span>
          {/* Days, never a date: a template outlives the calendar it was
              written against, and "two weeks after it starts" survives. */}
          <input type="number" min="0" value={form.default_due_in_days}
            onChange={set('default_due_in_days')} placeholder="e.g. 14" />
        </label>
      </div>

      <fieldset className="task-fieldset">
        <legend>Checklist</legend>
        <ul className="task-edit-list">
          {items.map((text, index) => (
            <li key={`item-${index}`}>
              <input value={text} aria-label={`Checklist line ${index + 1}`}
                onChange={(e) => setItems(
                  items.map((t, i) => (i === index ? e.target.value : t)))} />
              <button type="button" aria-label={`Remove ${text}`}
                onClick={() => setItems(items.filter((_, i) => i !== index))}>
                <X size={12} />
              </button>
            </li>
          ))}
        </ul>
        <div className="task-form-row">
          <label className="lr-field" style={{ flex: 1 }}>
            <span className="sr-only">New checklist line</span>
            <input value={draft} placeholder="Add a line…"
              aria-label="New checklist line"
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key !== 'Enter') return;
                e.preventDefault();
                if (!draft.trim()) return;
                setItems([...items, draft.trim()]);
                setDraft('');
              }} />
          </label>
          <button type="button" className="lr-btn" onClick={() => {
            if (!draft.trim()) return;
            setItems([...items, draft.trim()]);
            setDraft('');
          }}>
            <Plus size={14} /> Add line
          </button>
        </div>

        {sections.map((section, gi) => (
          <div key={`section-${gi}`} className="task-group">
            <label className="lr-field">
              <span>Section {gi + 1}</span>
              <input value={section.title} aria-label={`Section ${gi + 1} title`}
                onChange={(e) => {
                  const next = [...sections];
                  next[gi] = { ...next[gi], title: e.target.value };
                  setSections(next);
                }} />
            </label>
            <ul className="task-edit-list">
              {section.items.map((text, ii) => (
                <li key={`section-${gi}-${ii}`}>
                  <input value={text}
                    aria-label={`${section.title} line ${ii + 1}`}
                    onChange={(e) => {
                      const next = [...sections];
                      next[gi] = {
                        ...next[gi],
                        items: next[gi].items.map((t, i) => (i === ii ? e.target.value : t)),
                      };
                      setSections(next);
                    }} />
                  <button type="button" aria-label={`Remove ${text}`}
                    onClick={() => {
                      const next = [...sections];
                      next[gi] = {
                        ...next[gi],
                        items: next[gi].items.filter((_, i) => i !== ii),
                      };
                      setSections(next);
                    }}>
                    <X size={12} />
                  </button>
                </li>
              ))}
            </ul>
            <button type="button" className="lr-btn"
              onClick={() => {
                const next = [...sections];
                next[gi] = { ...next[gi], items: [...next[gi].items, 'New line'] };
                setSections(next);
              }}>
              <Plus size={14} /> Add line to {section.title || 'section'}
            </button>
          </div>
        ))}

        <button type="button" className="lr-btn" style={{ marginTop: 10 }}
          onClick={() => setSections([...sections, { title: 'New section', items: [] }])}>
          <Plus size={14} /> Add section
        </button>
      </fieldset>

      <div className="task-form-actions">
        <button type="button" className="lr-btn lr-btn-ghost" onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" className="lr-btn lr-btn-primary" disabled={saving}>
          {saving ? 'Saving…' : 'Create Template'}
        </button>
      </div>
    </form>
  );
};

const TaskTemplates = () => {
  const { role } = useAuth();
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState(null);
  const canManage = can(role, 'assignTask');

  const { data, isLoading, isError, error: loadError, refetch } = useQuery({
    queryKey: ['task-templates'],
    queryFn: () => taskService.getTemplates(),
  });

  const save = useMutation({
    mutationFn: (payload) => taskService.createTemplate(payload),
    onSuccess: () => {
      setError(null);
      setCreating(false);
      queryClient.invalidateQueries({ queryKey: ['task-templates'] });
    },
    onError: (err) => setError(err?.response?.data
      || { detail: 'The template could not be saved.' }),
  });

  const retire = useMutation({
    mutationFn: (templateId) => taskService.retireTemplate(templateId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['task-templates'] }),
  });

  if (isLoading) return <div className="page"><Skeleton rows={4} /></div>;
  if (isError) {
    return <div className="page"><ErrorState error={loadError} onRetry={refetch} /></div>;
  }

  const rows = data?.results ?? data ?? [];

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">Task Templates</h1>
          <p className="lr-page-sub">
            Reusable task shapes — applying one copies its checklist, it does not
            link to it
          </p>
        </div>
        {canManage && !creating && (
          <button type="button" className="lr-btn lr-btn-primary"
            onClick={() => setCreating(true)}>
            <Plus size={14} /> New Template
          </button>
        )}
      </div>

      {error?.detail && (
        <div className="lr-error" role="alert">
          <p className="lr-error-msg">{error.detail}</p>
        </div>
      )}

      {creating && (
        <Editor onCancel={() => { setCreating(false); setError(null); }}
          onSave={(payload) => save.mutate(payload)}
          saving={save.isPending} error={error} />
      )}

      {rows.length === 0 && !creating && (
        <p className="task-sub">
          <LayoutTemplate size={13} aria-hidden="true" /> No templates yet.
          {canManage && ' Create one, or save an existing task as a template.'}
        </p>
      )}

      {rows.length > 0 && (
        <div className="lr-table-wrap">
          <table className="lr-table">
            <thead>
              <tr>
                <th scope="col">Template</th>
                <th scope="col">Priority</th>
                <th scope="col">Checklist</th>
                <th scope="col">Due In</th>
                <th scope="col">Used</th>
                {canManage && <th scope="col">Action</th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td>
                    <div style={{ fontWeight: 600 }}>{row.name}</div>
                    {row.description && (
                      <div className="task-sub">{row.description}</div>
                    )}
                  </td>
                  <td>
                    <span className={`min-status is-${priorityTone(row.priority)}`}>
                      {row.priority_label}
                    </span>
                  </td>
                  <td>{row.item_count} item{row.item_count === 1 ? '' : 's'}</td>
                  <td>
                    {row.default_due_in_days === null ? '—'
                      : `${row.default_due_in_days} days`}
                  </td>
                  <td style={{ fontVariantNumeric: 'tabular-nums' }}>
                    {row.usage_count}
                  </td>
                  {canManage && (
                    <td>
                      <button type="button" className="task-link-btn"
                        disabled={retire.isPending}
                        onClick={() => retire.mutate(row.id)}>
                        <Archive size={12} /> Retire
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default TaskTemplates;
