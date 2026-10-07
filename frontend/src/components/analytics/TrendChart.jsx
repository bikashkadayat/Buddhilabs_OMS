import React, { useMemo } from 'react';
import {
  Area, AreaChart, CartesianGrid, Legend, Line, LineChart, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import {
  AREA, AXIS, CHART_MARGIN, GRID, LEGEND, LINE, TOOLTIP,
} from './chartTheme';

/**
 * The line/area workhorse: N named series over one shared x-axis.
 *
 * One y-axis, always. Two measures on different scales get two charts or an
 * index to a common base — a second axis lets the reader infer any correlation
 * the author feels like implying.
 *
 * The reshape is memoised on `data`. Recharts re-renders on every parent state
 * change, and rebuilding a 36-point series each time is the usual cause of jank
 * on a dashboard that also polls.
 */
const TrendChart = ({
  data = [], series = [], stacked = false, area = false, height = 260,
  yUnit = '', domain, referenceValue, referenceLabel, xKey = 'label',
}) => {
  const points = useMemo(() => data.map((row) => ({ ...row })), [data]);
  const Chart = area ? AreaChart : LineChart;
  const showLegend = series.length > 1;

  return (
    <ResponsiveContainer width="100%" height={height}>
      <Chart data={points} margin={CHART_MARGIN}>
        <CartesianGrid {...GRID} />
        <XAxis dataKey={xKey} {...AXIS} interval="preserveStartEnd" minTickGap={16} />
        <YAxis
          {...AXIS}
          domain={domain}
          width={48}
          tickFormatter={(value) => `${value}${yUnit}`}
        />
        <Tooltip
          {...TOOLTIP}
          formatter={(value, name) => [
            value === null || value === undefined ? '—' : `${value}${yUnit}`, name]}
        />
        {showLegend && <Legend {...LEGEND} />}
        {referenceValue !== undefined && referenceValue !== null && (
          <ReferenceLine
            y={referenceValue} stroke="var(--text-muted, #94a3b8)"
            strokeDasharray="4 4"
            label={{ value: referenceLabel, position: 'right', fontSize: 'var(--fs-label)',
                     fill: 'var(--text-secondary)' }}
          />
        )}
        {series.map((entry) => (area ? (
          <Area
            key={entry.key} type="monotone" dataKey={entry.key} name={entry.label}
            stackId={stacked ? 'stack' : undefined}
            stroke={entry.colour} fill={entry.colour}
            // A 2px surface gap between stacked fills, so adjacent segments
            // read as separate bands rather than one blended mass.
            strokeWidth={stacked ? 2 : AREA.strokeWidth}
            fillOpacity={AREA.fillOpacity}
            connectNulls={false}
          />
        ) : (
          <Line
            key={entry.key} type="monotone" dataKey={entry.key} name={entry.label}
            stroke={entry.colour} {...LINE}
            strokeDasharray={entry.dashed ? '5 4' : undefined}
            connectNulls={false}
          />
        )))}
      </Chart>
    </ResponsiveContainer>
  );
};

export default React.memo(TrendChart);
