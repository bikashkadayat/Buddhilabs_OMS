import { useMeasure } from '../../hooks/useMeasure'
import { COLOR } from './common'

/** 12-week trend behind the hero figure. No axes, no labels -- shape only. */
export function Sparkline({ dayList, height = 44, weeks = 12 }) {
  const [ref, width] = useMeasure()
  const points = dayList.slice(-weeks * 7)

  return (
    <div className="spark" ref={ref}>
      {width > 0 && points.length > 1 ? (() => {
        const max = Math.max(...points.map((p) => p.present))
        const x = (i) => (i / (points.length - 1)) * width
        const y = (v) => height - 2 - (v / max) * (height - 6)
        const line = points.map((p, i) => `${x(i)},${y(p.present)}`).join(' ')
        return (
          <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height}>
            <polygon points={`0,${height} ${line} ${width},${height}`} style={{ fill: COLOR.series1 }} fillOpacity={0.1} />
            <polyline points={line} fill="none" strokeWidth={2} strokeLinejoin="round" style={{ stroke: COLOR.series1 }} />
          </svg>
        )
      })() : null}
    </div>
  )
}
