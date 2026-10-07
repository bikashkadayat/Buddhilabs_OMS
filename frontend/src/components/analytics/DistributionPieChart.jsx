import React, { useMemo } from 'react';
import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts';
import { LEGEND, OTHERS_COLOUR, TOOLTIP, seriesColour } from './chartTheme';

/**
 * Share-of-total for a handful of categories.
 *
 * Used sparingly and only where the parts genuinely sum to a whole (leave type
 * mix, device status). Anything past six slices is folded into "Other": a pie
 * with nine wedges is a bar chart that has been made harder to read.
 *
 * Leave types bring their own colour from `LeaveType.display_color`, which HR
 * maintains — a type that is purple in the leave calendar must be purple here.
 */
const MAX_SLICES = 6;

const DistributionPieChart = ({
  data = [], valueKey = 'days', labelKey = 'label', height = 260, unit = '',
}) => {
  const slices = useMemo(() => {
    const sorted = [...data].sort((a, b) => (b[valueKey] || 0) - (a[valueKey] || 0));
    const head = sorted.slice(0, MAX_SLICES);
    const tail = sorted.slice(MAX_SLICES);
    if (!tail.length) return head;
    return [...head, {
      [labelKey]: `Other (${tail.length})`,
      [valueKey]: tail.reduce((total, row) => total + (row[valueKey] || 0), 0),
      colour: OTHERS_COLOUR,
      isAggregate: true,
    }];
  }, [data, valueKey, labelKey]);

  const total = slices.reduce((sum, row) => sum + (row[valueKey] || 0), 0);
  if (!total) return null;

  return (
    <ResponsiveContainer width="100%" height={height}>
      <PieChart>
        <Pie
          data={slices} dataKey={valueKey} nameKey={labelKey}
          cx="50%" cy="50%" innerRadius="52%" outerRadius="80%"
          // A 2px surface ring between wedges, so adjacent slices stay distinct
          // even where two hues sit close together.
          stroke="var(--bg-card, #fff)" strokeWidth={2}
          isAnimationActive={false}
        >
          {slices.map((row, index) => (
            <Cell
              key={row[labelKey]}
              fill={row.colour || seriesColour(index, row.isAggregate)}
            />
          ))}
        </Pie>
        <Tooltip
          {...TOOLTIP}
          formatter={(value, name) => [
            `${value}${unit} · ${((value / total) * 100).toFixed(1)}%`, name]}
        />
        <Legend {...LEGEND} />
      </PieChart>
    </ResponsiveContainer>
  );
};

export default React.memo(DistributionPieChart);
