import { useState } from 'react'
import { fmtInt } from '../../lib/format'
import { WEEKDAYS } from '../../lib/time'
import { useTooltip } from '../Tooltip'
import { COLOR, ChartFrame, EmptyChart, ScaleLegend, seqStep } from './common'

const CELL_HEIGHT = 24

export function HeatmapChart({ view }) {
  const tip = useTooltip()
  const [hover, setHover] = useState(null)

  const max = Math.max(...view.hourWeekday.flat())
  if (!max) return <EmptyChart message="No punches in this range." />

  return (
    <>
      <ChartFrame height={7 * CELL_HEIGHT + 30} margin={{ left: 44, right: 8, top: 16, bottom: 14 }}>
        {({ iw }) => {
          const cellW = iw / 24
          return (
            <>
              {Array.from({ length: 24 }, (_, hour) => (hour % 2 === 0 ? (
                <text className="axis-text" key={hour} x={hour * cellW + cellW / 2} y={-4} textAnchor="middle">
                  {String(hour).padStart(2, '0')}
                </text>
              ) : null))}

              {view.hourWeekday.map((row, weekday) => (
                <g key={weekday}>
                  <text
                    className="axis-text strong" x={-10}
                    y={weekday * CELL_HEIGHT + CELL_HEIGHT / 2 + 4} textAnchor="end"
                  >
                    {WEEKDAYS[weekday]}
                  </text>
                  {row.map((value, hour) => {
                    const key = `${weekday}-${hour}`
                    return (
                      <g key={key}>
                        {/* 1px inset each side = a 2px surface gap between cells.
                            White does the separating, never a stroke. */}
                        <rect
                          x={hour * cellW + 1} y={weekday * CELL_HEIGHT + 1}
                          width={Math.max(1, cellW - 2)} height={CELL_HEIGHT - 2} rx={2}
                          style={{
                            fill: COLOR.seq(seqStep(value, max)),
                            stroke: hover === key ? COLOR.ink : 'none',
                            strokeWidth: 1,
                          }}
                        />
                        <rect
                          className="hit" x={hour * cellW} y={weekday * CELL_HEIGHT}
                          width={cellW} height={CELL_HEIGHT}
                          onPointerMove={(event) => {
                            setHover(key)
                            tip.show(event, `${WEEKDAYS[weekday]} ${String(hour).padStart(2, '0')}:00`,
                              [{ name: 'Punches', value: fmtInt(value) }])
                          }}
                          onPointerLeave={() => { setHover(null); tip.hide() }}
                        />
                      </g>
                    )
                  })}
                </g>
              ))}
            </>
          )
        }}
      </ChartFrame>
      <ScaleLegend label="Punches per cell" max={max} format={fmtInt} />
    </>
  )
}

export const heatmapTable = (view) => ({
  headers: ['Weekday', ...Array.from({ length: 24 }, (_, h) => String(h).padStart(2, '0'))],
  rows: view.hourWeekday.map((row, weekday) => [WEEKDAYS[weekday], ...row.map(fmtInt)]),
})
