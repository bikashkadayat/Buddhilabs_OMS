import React, { useState } from 'react';
import { Table2, X } from 'lucide-react';

/**
 * The wrapper every analytics chart is rendered inside. Not optional.
 *
 * It carries three things a bare `<ResponsiveContainer>` cannot:
 *
 * 1. **A table view.** Two of the palettes in `chartTheme` sit under 3:1
 *    contrast against a white card in light mode. The rule for that is relief,
 *    not a colour change — a legend plus a readable table — and this is where
 *    the relief lives. It is also how anyone gets the numbers into a deck.
 * 2. **An honest empty state.** A chart with no data must say so. An empty plot
 *    area reads as "everything is zero", which is a different claim.
 * 3. **A note slot**, used for the things the API reports about its own output:
 *    a downgraded granularity, folded department series, a projection.
 */
const ChartFrame = ({
  title, subtitle, note, children, rows, columns, height = 260,
  empty = 'Nothing recorded in this window.', isEmpty = false, actions,
}) => {
  const [showTable, setShowTable] = useState(false);
  const canTabulate = Boolean(rows?.length && columns?.length);

  return (
    <section className="wf-card an-chart-card">
      <div className="wf-card-head">
        <div>
          <h2>{title}</h2>
          {subtitle && <p className="an-chart-sub">{subtitle}</p>}
        </div>
        <div className="an-chart-actions">
          {actions}
          {canTabulate && (
            <button
              type="button"
              className="an-linkbtn"
              aria-expanded={showTable}
              onClick={() => setShowTable((open) => !open)}
            >
              {showTable ? <X size={14} /> : <Table2 size={14} />}
              {showTable ? 'Hide data' : 'View data'}
            </button>
          )}
        </div>
      </div>

      {isEmpty ? (
        <p className="wf-muted an-chart-empty" role="status">{empty}</p>
      ) : (
        <div className="an-chart" style={{ height }}>{children}</div>
      )}

      {note && <p className="an-chart-note">{note}</p>}

      {showTable && canTabulate && (
        <div className="an-table-wrap">
          <table className="an-table">
            <caption className="sr-only">{title} — data table</caption>
            <thead>
              <tr>{columns.map((column) => (
                <th key={column.key} scope="col">{column.label}</th>
              ))}</tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={row.period ?? row.department_id ?? row.label ?? index}>
                  {columns.map((column) => (
                    <td key={column.key}>
                      {column.render ? column.render(row) : (row[column.key] ?? '—')}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
};

export default ChartFrame;
