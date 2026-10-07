import React from 'react';
import { Plus, X } from 'lucide-react';

import RichTextEditor from './RichTextEditor';

/**
 * The memo's content blocks (E-memo-manual p.3).
 *
 * The form opens with **Background** and **Recommendation**, and a "+ Add more"
 * control the manual describes as "Click here to add more field (title and
 * description)". So a memo body is an ordered list of (title, description) pairs
 * whose length the author chooses — not one fixed field.
 *
 * The red X on a block (p.5) removes it, including one of the two defaults: the
 * manual shows the control on the Background block itself, so nothing here treats
 * those two as undeletable.
 *
 * The list's ORDER is the document's order, which is why the whole list is handed
 * back on every change rather than each block owning its own save.
 *
 * @param {{ sections:Array<{title:string, body:string}>,
 *           onChange:(sections:Array)=>void, readOnly?:boolean,
 *           invalidIndex?:number }} props
 */
const MemoSectionsEditor = ({
  sections = [], onChange, readOnly = false, invalidIndex = -1,
}) => {
  const update = (index, patch) => onChange(
    sections.map((section, i) => (i === index ? { ...section, ...patch } : section)));

  const remove = (index) => onChange(sections.filter((_, i) => i !== index));

  /**
   * A NEW BLOCK IS BORN VALID.
   *
   * This used to append `{ title: '', body: '' }`. MemoSection.title is a
   * required column, so every author who clicked "+ Add more", wrote the
   * description and left the heading alone had their whole memo rejected —
   * after the memo row had already been created, orphaning a draft each time.
   * The manual's control promises "title and description"; supplying a real
   * default keeps that promise instead of handing back an invalid row.
   *
   * Numbered from the block's position, so the first extra block on the
   * manual's two defaults is "Section 3".
   */
  const add = () => onChange([
    ...sections, { title: `Section ${sections.length + 1}`, body: '' },
  ]);

  return (
    <section aria-labelledby="memo-sections-heading">
      <h3 className="memo-panel-title" id="memo-sections-heading">
        Memo content
        <span className="min-count">{sections.length}</span>
      </h3>

      {sections.map((section, index) => (
        // Keyed by position: a block has no id until it is saved, and keying by
        // title would remount the editor on every keystroke in the title box.
        <div className="memo-section-block" key={index}>
          <div className="memo-section-head">
            <input
              className="memo-section-title"
              value={section.title}
              readOnly={readOnly}
              aria-label={`Section ${index + 1} title`}
              placeholder="Title (e.g. Background)"
              // The title can still be emptied by hand, so the block says so
              // where the fault is rather than leaving the disabled Submit
              // button unexplained.
              aria-invalid={index === invalidIndex || undefined}
              aria-describedby={index === invalidIndex
                ? `memo-section-${index}-error` : undefined}
              onChange={(e) => update(index, { title: e.target.value })} />
            {!readOnly && (
              <button type="button" className="memo-icon-btn is-danger"
                aria-label={`Remove ${section.title || `section ${index + 1}`}`}
                onClick={() => remove(index)}>
                <X size={14} />
              </button>
            )}
          </div>
          {index === invalidIndex && (
            <p className="memo-form-error" role="alert"
              id={`memo-section-${index}-error`}>
              Section {index + 1} title is required.
            </p>
          )}
          <div className="memo-section-body">
            <span className="memo-sub-label">Description</span>
            <RichTextEditor
              value={section.body}
              readOnly={readOnly}
              onChange={(html) => update(index, { body: html })}
              placeholder="Write this section…" />
          </div>
        </div>
      ))}

      {!readOnly && (
        <button type="button" className="lr-btn memo-add-more" onClick={add}>
          <Plus size={14} /> Add more
        </button>
      )}
      {!readOnly && (
        <p className="lr-page-sub">Click here to add more field (title and description)</p>
      )}
    </section>
  );
};

export default MemoSectionsEditor;
