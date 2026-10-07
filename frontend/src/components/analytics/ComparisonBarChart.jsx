import React, { useMemo } from 'react';
import {
  Bar, BarChart, CartesianGrid, Cell, Legend, ReferenceLine, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts';
import {
  AXIS, BAR_RADIUS, BAR_RADIUS_H, CHART_MARGIN, GRID, LEGEND, TOOLTIP,
} from './chartTheme';

/**
 * Magnitude comparison across a small set of named things.
 *
 * Horizontal by default when the labels are department names: a vertical bar
 * chart with eight department names underneath it either rotates them to 45°
 * or truncates them, and both are worse than turning the chart on its side.
 *
 * `colourFor` receives the row so colour can follow the ENTITY (its stable
 * index, or a health band) rather than its position after sorting. Sorting a
 * chart must never repaint it.
 */
const ComparisonBarChart = ({
  data = [], bars = [], layout = 'horizontal', height = 280, yUnit = '',
  labelKey = 'label', colourFor, referenceValue, referenceLabel, domain,
}) => {
  const rows = useMemo(() => data.map((row) => ({ ...row })), [data]);
  const isHorizontal = layout === 'horizontal';
  const showLegend = bars.length > 1;

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart
        data={rows}
        layout={isHorizontal ? 'vertical' : 'horizontal'}
        margin={isHorizontal ? { top: 8, right: 24, left: 8, bottom: 0 } : CHART_MARGIN}
        // 2px of surface between adjacent bars, per the mark spec.
        barCategoryGap={isHorizontal ? '22%' : '28%'}
        barGap={2}
      >
        <CartesianGrid {...GRID} vertical={isHorizontal} horizontal={!isHorizontal} />
        {isHorizontal ? (
          <>
            <XAxis type="number" {...AXIS} domain={domain}
                   tickFormatter={(value) => `${value}${yUnit}`} />
            <YAxis type="category" dataKey={labelKey} {...AXIS} width={124}
                   interval={0} />
          </>
        ) : (
          <>
            <XAxis dataKey={labelKey} {...AXIS} interval="preserveStartEnd" />
            <YAxis {...AXIS} width={48} domain={domain}
                   tickFormatter={(value) => `${value}${yUnit}`} />
          </>
        )}
        <Tooltip
          {...TOOLTIP}
          cursor={{ fill: 'var(--border-light, #eef2f7)' }}
          formatter={(value, name) => [
            value === null || value === undefined ? '—' : `${value}${yUnit}`, name]}
        />
        {showLegend && <Legend {...LEGEND} />}
        {referenceValue !== undefined && referenceValue !== null && (
          <ReferenceLine
            {...(isHorizontal ? { x: referenceValue } : { y: referenceValue })}
            stroke="var(--text-muted, #94a3b8)" strokeDasharray="4 4"
            label={{ value: referenceLabel, position: 'top', fontSize: 'var(--fs-label)',
                     fill: 'var(--text-secondary)' }}
          />
        )}
        {bars.map((entry) => (
          <Bar
            key={entry.key} dataKey={entry.key} name={entry.label}
            fill={entry.colour} stackId={entry.stackId}
            radius={entry.stackId ? 0 : (isHorizontal ? BAR_RADIUS_H : BAR_RADIUS)}
            maxBarSize={isHorizontal ? 22 : 46}
          >
            {colourFor && rows.map((row, index) => (
              <Cell key={row[labelKey] ?? index} fill={colourFor(row, index)} />
            ))}
          </Bar>
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
};

export default React.memo(ComparisonBarChart);
