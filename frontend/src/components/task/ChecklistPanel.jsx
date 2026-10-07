import React, { useState } from 'react';
import { Check, ChevronDown, ChevronRight, Plus, X } from 'lucide-react';

import { fmtDate } from './taskLabels';

/**
 * The checklist (Phase T2.1), grouped or flat.
 *
 * WHY THE TICK AND THE PROGRESS BAR ARE THE SAME GESTURE
 * ------------------------------------------------------
 * Ticking a box returns the recalculated progress from the server, and the
 * caller applies it. There is no separate "now update the percentage" step,
 * because the boxes already say what the percentage is — asking for it twice is
 * how the two end up disagreeing.
 *
 * Sections are collapsible and default to open. A checklist people cannot see
 * is a checklist people do not do; collapsing is for a long list somebody has
 * finished with, not the resting state.
 */

const Row = ({ item, canTick, busy, onTick }) => (
  <li className="task-check-row">
    <label className="task-check">
      <input
        type="checkbox"
        checked={item.is_done}
        disabled={!canTick || busy}
        onChange={(e) => {
          // Read the value synchronously — this is a controlled checkbox, and
          // React resets it before an async handler could read it back.
          const next = e.target.checked;
          onTick(item.id, next);
        }}
      />
      <span className={item.is_done ? 'is-done' : ''}>{item.text}</span>
    </label>
    {item.is_done && item.done_by_name && (
      <span className="task-sub">
        {item.done_by_name} · {fmtDate(item.done_at)}
      </span>
    )}
  </li>
);

const Group = ({ group, canTick, busy, onTick }) => {
  const [open, setOpen] = useState(true);
  const complete = group.total_count > 0 && group.done_count === group.total_count;

  return (
    <li className="task-group">
      <button
        type="button"
        className="task-group-head"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="task-group-title">{group.title}</span>
        <span className={`task-group-tally${complete ? ' is-complete' : ''}`}>
          {complete && <Check size={12} aria-hidden="true" />}
          {group.done_count}/{group.total_count}
        </span>
      </button>
      {open && (
        <ul className="task-checklist">
          {group.items.map((item) => (
            <Row key={item.id} item={item} canTick={canTick} busy={busy}
              onTick={onTick} />
          ))}
          {group.items.length === 0 && (
            <li className="task-sub">This section is empty.</li>
          )}
        </ul>
      )}
    </li>
  );
};

/**
 * The editor, shown only to whoever may define the checklist.
 *
 * It edits a LOCAL copy and saves the whole list in one request, matching the
 * server's replace-the-whole-thing endpoint. Per-keystroke saving would fight
 * the tick-preservation rule, which is keyed on text: a half-typed line would
 * briefly be a different line and lose its tick.
 */
const Editor = ({ items, groups, onSave, saving, onCancel }) => {
  const [loose, setLoose] = useState(items.map((i) => i.text));
  const [sections, setSections] = useState(
    groups.map((g) => ({ title: g.title, items: g.items.map((i) => i.text) })),
  );
  const [draft, setDraft] = useState('');

  const addLoose = () => {
    if (!draft.trim()) return;
    setLoose([...loose, draft.trim()]);
    setDraft('');
  };

  const setSectionItem = (gi, ii, value) => {
    const next = [...sections];
    next[gi] = { ...next[gi], items: next[gi].items.map((t, i) => (i === ii ? value : t)) };
    setSections(next);
  };

  return (
    <div className="task-checklist-editor">
      <p className="task-sub">
        Lines without a section come first. Ticks are kept for any line whose
        wording you leave unchanged.
      </p>

      {/* Index-keyed: two lines may legitimately read the same, and these rows
          are only added and removed at the end while the editor is open, so
          React never reconciles a reordered list against a stale index. */}
      <ul className="task-edit-list">
        {loose.map((text, index) => (
          <li key={`loose-${index}`}>
            <input
              value={text}
              aria-label={`Checklist line ${index + 1}`}
              onChange={(e) => setLoose(loose.map((t, i) => (i === index ? e.target.value : t)))}
            />
            <button type="button" aria-label={`Remove ${text}`}
              onClick={() => setLoose(loose.filter((_, i) => i !== index))}>
              <X size={12} />
            </button>
          </li>
        ))}
      </ul>

      <div className="task-form-row">
        <label className="lr-field" style={{ flex: 1 }}>
          <span className="sr-only">New checklist line</span>
          <input value={draft} placeholder="Add a line…" aria-label="New checklist line"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              // Enter adds the line; it must not submit anything.
              if (e.key !== 'Enter') return;
              e.preventDefault();
              addLoose();
            }} />
        </label>
        <button type="button" className="lr-btn" onClick={addLoose}>
          <Plus size={14} /> Add line
        </button>
      </div>

      {sections.map((section, gi) => (
        <fieldset key={`section-${gi}`} className="task-fieldset">
          <legend>
            <input
              value={section.title}
              aria-label={`Section ${gi + 1} title`}
              onChange={(e) => {
                const next = [...sections];
                next[gi] = { ...next[gi], title: e.target.value };
                setSections(next);
              }}
            />
          </legend>
          <ul className="task-edit-list">
            {section.items.map((text, ii) => (
                  <li key={`section-${gi}-${ii}`}>
                <input value={text} aria-label={`${section.title} line ${ii + 1}`}
                  onChange={(e) => setSectionItem(gi, ii, e.target.value)} />
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
          <div className="task-form-actions">
            {/* Named after its section: two buttons both reading "Add line"
                are ambiguous on screen and unusable with a screen reader. */}
            <button type="button" className="lr-btn"
              onClick={() => {
                const next = [...sections];
                next[gi] = { ...next[gi], items: [...next[gi].items, 'New line'] };
                setSections(next);
              }}>
              <Plus size={14} /> Add line to {section.title || 'section'}
            </button>
            <button type="button" className="lr-btn lr-btn-ghost"
              onClick={() => setSections(sections.filter((_, i) => i !== gi))}>
              Remove {section.title || 'section'}
            </button>
          </div>
        </fieldset>
      ))}

      <div className="task-form-actions">
        <button type="button" className="lr-btn"
          onClick={() => setSections([...sections, { title: 'New section', items: [] }])}>
          <Plus size={14} /> Add section
        </button>
        <button type="button" className="lr-btn lr-btn-ghost" onClick={onCancel}>
          Cancel
        </button>
        <button type="button" className="lr-btn lr-btn-primary" disabled={saving}
          onClick={() => onSave({
            items: loose.map((t) => t.trim()).filter(Boolean),
            groups: sections
              .map((s) => ({
                title: s.title.trim() || 'Section',
                items: s.items.map((t) => t.trim()).filter(Boolean),
              }))
              // A section with no lines renders as an empty heading forever.
              .filter((s) => s.items.length > 0),
          })}>
          {saving ? 'Saving…' : 'Save checklist'}
        </button>
      </div>
    </div>
  );
};

const ChecklistPanel = ({
  items = [], groups = [], canTick, canManage, busy, onTick, onSave,
}) => {
  const [editing, setEditing] = useState(false);
  const empty = items.length === 0 && groups.length === 0;

  if (editing) {
    return (
      <Editor items={items} groups={groups} saving={busy}
        onCancel={() => setEditing(false)}
        onSave={(payload) => { onSave(payload); setEditing(false); }} />
    );
  }

  return (
    <>
      {empty && <p className="task-sub">No checklist on this task.</p>}
      <ul className="task-checklist">
        {items.map((item) => (
          <Row key={item.id} item={item} canTick={canTick} busy={busy}
            onTick={onTick} />
        ))}
      </ul>
      <ul className="task-groups">
        {groups.map((group) => (
          <Group key={group.id} group={group} canTick={canTick} busy={busy}
            onTick={onTick} />
        ))}
      </ul>
      {canManage && (
        <button type="button" className="lr-btn" onClick={() => setEditing(true)}>
          {empty ? 'Add a checklist' : 'Edit checklist'}
        </button>
      )}
    </>
  );
};

export default ChecklistPanel;
