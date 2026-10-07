import { useState } from 'react'
import { fmtCompact, fmtInt, ticks } from '../../lib/format'
import { useTooltip } from '../Tooltip'
import { Baseline, COLOR, ChartFrame, EmptyChart, Legend, YAxis, roundedColumn } from './common'

const hourLabel = (hour) => `${String(hour).padStart(2, '0')}:00`

export function ClockChart({ view }) {
  const tip = useTooltip()
  const [hover, setHover] = useState(null)

  const total = view.arrivalHist.reduce((a, b) => a + b, 0)
  if (!total) return <EmptyChart message="No punches in this range." />

  const yTicks = ticks(Math.max(1, ...view.arrivalHist, ...view.departureHist))
  const top = yTicks[yTicks.length - 1]

  return (
    <>
      <Legend items={[
        { label: 'First punch (arrival)', color: COLOR.series1, box: true },
        { label: 'Last punch (departure)', color: COLOR.series2, box: true },
      ]}
      />

      <ChartFrame height={240} margin={{ left: 40, right: 12, top: 12, bottom: 28 }}>
        {({ iw, ih }) => {
          const band = iw / 24
          // -1 each side of centre leaves a 2px surface gap inside the pair.
          const barWidth = Math.min(11, (band - 2) / 2 - 1)
          const y = (v) => ih - (v / top) * ih

          return (
            <>
              <YAxis ticks={yTicks} scale={y} width={iw} format={fmtCompact} />

              {Array.from({ length: 24 }, (_, hour) => {
                const centre = hour * band + band / 2
                const pairs = [
                  { value: view.arrivalHist[hour], color: COLOR.series1, name: 'Arrivals', x: centre - barWidth - 1 },
                  { value: view.departureHist[hour], color: COLOR.series2, name: 'Departures', x: centre + 1 },
                ]
                return (
                  <g key={hour}>
                    {pairs.map((pair) => (pair.value ? (
                      <path
                        key={pair.name}
                        d={roundedColumn(pair.x, ih - Math.max(2, ih - y(pair.value)), barWidth, Math.max(2, ih - y(pair.value)))}
                        className={hover === hour ? 'mark-hover' : undefined}
                        style={{ fill: pair.color }}
                      />
                    ) : null))}

                    {hour % 3 === 0 ? (
                      <text className="axis-text" x={centre} y={ih + 18} textAnchor="middle">
                        {String(hour).padStart(2, '0')}
                      </text>
                    ) : null}

                    <rect
                      className="hit" x={hour * band} y={0} width={band} height={ih}
                      onPointerMove={(event) => {
                        setHover(hour)
                        tip.show(event, `${hourLabel(hour)} – ${String(hour).padStart(2, '0')}:59`,
                          pairs.map((pair) => ({ color: pair.color, name: pair.name, value: fmtInt(pair.value) })))
                      }}
                      onPointerLeave={() => { setHover(null); tip.hide() }}
                    />
                  </g>
                )
              })}

              <Baseline x1={0} x2={iw} y1={ih} y2={ih} />
            </>
          )
        }}
      </ChartFrame>
    </>
  )
}

export const clockTable = (view) => ({
  headers: ['Hour', 'Arrivals', 'Departures'],
  rows: view.arrivalHist.map((value, hour) => [hourLabel(hour), fmtInt(value), fmtInt(view.departureHist[hour])]),
})
