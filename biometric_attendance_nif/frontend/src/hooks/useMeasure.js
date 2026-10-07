import { useLayoutEffect, useRef, useState } from 'react'

/* Charts need a pixel width to lay out an SVG viewBox.
 *
 * The first measurement is taken synchronously in a layout effect rather than
 * waiting for the ResizeObserver's first callback: waiting leaves a frame with
 * an empty card, and some renderers (headless Chrome among them) may not
 * deliver that first callback at all. The observer then keeps the width honest
 * through window resizes, drawer opens and tab switches -- a window 'resize'
 * listener would miss the last two.
 */
export function useMeasure() {
  const ref = useRef(null) 
  const [width, setWidth] = useState(0)

  useLayoutEffect(() => {
    const node = ref.current
    if (!node) return undefined

    const measure = () => {
      const next = Math.floor(node.getBoundingClientRect().width)
      // Ignore zero -- that's a hidden tab, not a real layout.
      if (next > 0) setWidth((prev) => (prev === next ? prev : next))
    }

    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  return [ref, width]
}
