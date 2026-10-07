import React from 'react';
import Skeleton from './Skeleton';
import EmptyState from './EmptyState';

/**
 * One table (Phase D / blueprint §21).
 *
 * Replaces per-page table markup whose headers, padding and zebra striping had
 * all diverged. Loading and empty are handled HERE rather than by each caller,
 * which is what stopped them being handled at all in several places.
 *
 * Wrapped in its own `overflow-x: auto` container: a wide table must scroll
 * inside itself, never make the whole page scroll sideways.
 *
 * Columns: { key, header, width?, align?, render?(row) }
 */
const DataTable = ({
  columns = [], rows = [], loading = false, empty, caption, rowKey = (r, i) => r.id ?? i,
}) => {
  if (loading) return <Skeleton rows={5} height={14} label="Loading table" />;
  if (!rows.length && empty) return <EmptyState {...empty} />;

  return (
    <div className="ui-table-wrap">
      <table className="ui-table">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead>
          <tr>
            {columns.map((c) => (
              <th
                key={c.key}
                scope="col"
                style={{ width: c.width, textAlign: c.align || 'left' }}
              >
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={rowKey(row, i)}>
              {columns.map((c) => (
                <td key={c.key} style={{ textAlign: c.align || 'left' }}>
                  {c.render ? c.render(row) : row[c.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

export default DataTable;
