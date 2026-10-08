import { useState } from 'react'

/* Data-quality notes are collapsed to a single chip. They matter -- a file with
 * every punch in it twice would double every figure -- but they are not what
 * the reader came for, so they stay one click away instead of taking a
 * paragraph at the top of every visit.
 */
export function NotesBanner({ notes }) {
  const [open, setOpen] = useState(false)
  if (!notes || !notes.length) return null

  return (
    <div className={open ? 'notes open' : 'notes'}>
      <button type="button" className="notes-toggle" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <span className="notes-dot" />
        {`${notes.length} note${notes.length === 1 ? '' : 's'} about this data`}
        <span className="caret">{open ? '▴' : '▾'}</span>
      </button>
      {open ? <ul>{notes.map((note) => <li key={note}>{note}</li>)}</ul> : null}
    </div>
  )
}
