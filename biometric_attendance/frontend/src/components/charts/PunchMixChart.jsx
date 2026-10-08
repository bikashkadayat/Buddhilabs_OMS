import { useState } from 'react'
import { fmt1, fmtInt } from '../../lib/format'
import { punchLabel } from '../../lib/time'
import { useTooltip } from '../Tooltip'
import { COLOR, ChartFrame, EmptyChart, roundedBar } from './common'

const ROW_HEIGHT = 46
const BAR_HEIGHT = 24   // the cap -- the rest of the row's height is air

/* One series, so no legend: the card title already says what is plotted, and a
   box with a single swatch would just restate it. */
export function PunchMixChart({ view }) {
  const tip = useTooltip()
  const [hover, setHover] = useState(null)

  if (!view.punchMix.length) return <EmptyChart message="No punches in this range." />

  const max = Math.max(...view.punchMix.map(([, count]) => count))

  return (
    <ChartFrame
      height={view.punchMix.length * ROW_HEIGHT + 16}
      margin={{ left: 96, right: 84, top: 8, bottom: 8 }}
    >
      {({ iw }) => (
        <>
          {view.punchMix.map(([code, count], i) => {
            const y = i * ROW_HEIGHT + (ROW_HEIGHT - BAR_HEIGHT) / 2
            const width = Math.max(2, (count / max) * iw)
            const share = view.total ? (count / view.total) * 100 : 0
            const label = punchLabel(code)

            return (
              <g key={code}>
                <text className="label-text" x={-10} y={y + BAR_HEIGHT / 2 + 4} textAnchor="end">{label}</text>
                <path
                  d={roundedBar(0, y, width, BAR_HEIGHT)}
                  className={hover === code ? 'mark-hover' : undefined}
                  style={{ fill: COLOR.series1 }}
                />
                <text className="value-text" x={width + 8} y={y + BAR_HEIGHT / 2 + 1}>{fmtInt(count)}</text>
                <text className="axis-text" x={width + 8} y={y + BAR_HEIGHT / 2 + 14}>{`${fmt1(share)}%`}</text>

                <rect
                  className="hit" x={-96} y={i * ROW_HEIGHT} width={iw + 96} height={ROW_HEIGHT}
                  onPointerMove={(event) => {
                    setHover(code)
                    tip.show(event, label, [
                      { color: COLOR.series1, name: 'Punches', value: fmtInt(count) },
                      { name: 'Share', value: `${fmt1(share)}%` },
                    ])
                  }}
                  onPointerLeave={() => { setHover(null); tip.hide() }}
                />
              </g>
            )
          })}
          <line className="baseline" x1={0} x2={0} y1={0} y2={view.punchMix.length * ROW_HEIGHT} />
        </>
      )}
    </ChartFrame>
  )
}

export const punchMixTable = (view) => ({
  headers: ['Punch type', 'Count', 'Share'],
  rows: view.punchMix.map(([code, count]) => [
    punchLabel(code), fmtInt(count), `${fmt1(view.total ? (count / view.total) * 100 : 0)}%`,
  ]),
})
