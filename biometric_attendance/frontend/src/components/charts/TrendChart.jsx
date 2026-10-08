import { useState } from 'react'
import { bucketTrend } from '../../lib/analytics'
import { clamp, fmt1, fmtInt, ticks } from '../../lib/format'
import { fmtDay } from '../../lib/time'
import { useTooltip } from '../Tooltip'
import { Baseline, COLOR, ChartFrame, EmptyChart, Legend, YAxis } from './common'

export function TrendChart({ view }) {
  const tip = useTooltip()
  const [hover, setHover] = useState(null)

  if (!view.dayList.length) return <EmptyChart message="No punches in this range." />

  const { unit, points } = bucketTrend(view.dayList)
  const daily = unit === 'day'

  // A 7-day average only means something at daily resolution.
  const average = daily
    ? points.map((_, i) => {
      const window = points.slice(Math.max(0, i - 6), i + 1)
      return window.reduce((sum, p) => sum + p.value, 0) / window.length
    })
    : null

  const yTicks = ticks(Math.max(1, ...points.map((p) => p.value)))
  const top = yTicks[yTicks.length - 1]

  return (
    <>
      <Legend items={daily
        ? [
          { label: 'People present that day', color: COLOR.series1, box: true },
          { label: '7-day average', color: COLOR.series2 },
        ]
        : [{ label: `People present per day (${unit}ly average)`, color: COLOR.series1, box: true }]}
      />

      <ChartFrame height={260} margin={{ left: 44, right: 16, top: 14, bottom: 28 }}>
        {({ iw, ih }) => {
          const x = (i) => (points.length === 1 ? iw / 2 : (i / (points.length - 1)) * iw)
          const y = (v) => ih - (v / top) * ih
          const line = points.map((p, i) => `${x(i)},${y(p.value)}`).join(' ')
          const step = Math.max(1, Math.ceil(points.length / 6))
          const labelled = points.map((_, i) => i).filter((i) => (points.length - 1 - i) % step === 0)

          const onMove = (event) => {
            // Measure the hit rect itself: it spans exactly 0..iw, so this
            // stays correct however the viewBox is scaled into the page.
            const box = event.currentTarget.getBoundingClientRect()
            const ratio = box.width ? (event.clientX - box.left) / box.width : 0
            const i = clamp(Math.round(ratio * (points.length - 1)), 0, points.length - 1)
            setHover(i)
            const rows = [{ color: COLOR.series1, name: 'Present', value: daily ? fmtInt(points[i].value) : fmt1(points[i].value) }]
            if (average) rows.push({ color: COLOR.series2, name: '7-day avg', value: fmt1(average[i]) })
            rows.push({ name: 'Punches', value: fmtInt(points[i].punches) })
            if (!daily) rows.push({ name: 'Active days', value: fmtInt(points[i].days) })
            tip.show(event, points[i].label, rows)
          }

          return (
            <>
              <YAxis ticks={yTicks} scale={y} width={iw} format={fmtInt} />

              <polygon points={`0,${ih} ${line} ${x(points.length - 1)},${ih}`} style={{ fill: COLOR.series1 }} fillOpacity={0.1} />
              <polyline
                points={line} fill="none" strokeWidth={daily ? 1.5 : 2}
                strokeLinejoin="round" strokeLinecap="round" style={{ stroke: COLOR.series1 }}
              />
              {average ? (
                <polyline
                  points={average.map((v, i) => `${x(i)},${y(v)}`).join(' ')}
                  fill="none" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round"
                  style={{ stroke: COLOR.series2 }}
                />
              ) : null}

              {labelled.map((i) => (
                <text className="axis-text" key={i} x={x(i)} y={ih + 18} textAnchor="middle">
                  {unit === 'month' ? points[i].label : fmtDay(daily ? points[i].x : points[i].x * 7, false)}
                </text>
              ))}
              <Baseline x1={0} x2={iw} y1={ih} y2={ih} />

              {/* The crosshair finds the X -- readers aim at a date, never at a 2px line. */}
              {hover !== null ? (
                <g pointerEvents="none">
                  <line className="crosshair" x1={x(hover)} x2={x(hover)} y1={0} y2={ih} />
                  <circle
                    cx={x(hover)} cy={y(points[hover].value)} r={4.5} strokeWidth={2}
                    style={{ fill: COLOR.series1, stroke: COLOR.surface }}
                  />
                </g>
              ) : null}

              <rect
                className="hit" x={0} y={0} width={iw} height={ih}
                onPointerMove={onMove}
                onPointerLeave={() => { setHover(null); tip.hide() }}
              />
            </>
          )
        }}
      </ChartFrame>
    </>
  )
}

export const trendTable = (view) => ({
  headers: ['Date', 'People present', 'Punches'],
  rows: view.dayList.slice().reverse().map((d) => [fmtDay(d.day), fmtInt(d.present), fmtInt(d.punches)]),
})
