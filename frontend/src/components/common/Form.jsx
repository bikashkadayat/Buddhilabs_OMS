import React, { useId } from 'react';

/**
 * Shared form parts (Phase D / blueprint §21).
 *
 * The point is the label/control/error wiring, which is what hand-rolled forms
 * get wrong: `useId` gives every field a real `htmlFor`, an error is bound with
 * `aria-describedby` and announced, and a required field is marked for
 * assistive technology rather than only with a red asterisk.
 */

export const FormSection = ({ title, description, children }) => (
  <section className="ui-fsec">
    {title && <h2 className="ui-fsec-title">{title}</h2>}
    {description && <p className="ui-fsec-desc">{description}</p>}
    <div className="ui-fsec-body">{children}</div>
  </section>
);

export const FormHint = ({ children, id }) => (
  <p className="ui-fhint" id={id}>{children}</p>
);

export const FormRow = ({
  label, hint, error, required = false, children, htmlFor,
}) => {
  const auto = useId();
  const id = htmlFor || auto;
  const hintId = hint ? `${id}-hint` : undefined;
  const errId = error ? `${id}-err` : undefined;
  const describedBy = [hintId, errId].filter(Boolean).join(' ') || undefined;

  return (
    <div className={`ui-frow${error ? ' has-error' : ''}`}>
      <label className="ui-flabel" htmlFor={id}>
        {label}
        {required && <span className="ui-freq" aria-hidden="true"> *</span>}
        {required && <span className="sr-only"> (required)</span>}
      </label>
      {/* The control receives the id and the description wiring, so a caller
          cannot forget them. */}
      {typeof children === 'function'
        ? children({ id, 'aria-describedby': describedBy, 'aria-invalid': error ? true : undefined, required })
        : children}
      {hint && <FormHint id={hintId}>{hint}</FormHint>}
      {error && <p className="ui-ferr" id={errId} role="alert">{error}</p>}
    </div>
  );
};

export const FormActions = ({ children, align = 'end' }) => (
  <div className={`ui-factions is-${align}`}>{children}</div>
);

export default { FormRow, FormActions, FormSection, FormHint };
