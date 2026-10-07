import { useCallback, useEffect, useRef, useState } from 'react'

const POLL_MS = 20000

/* Loads the punch log once, then polls the cheap /api/meta endpoint to notice
 * when the collector has written something new. Only a changed revision costs
 * a refetch of the (much larger) dataset, so a dashboard left open all day is
 * a `stat` every 20 seconds rather than a reparse.
 */
export function useDataset() {
  const [payload, setPayload] = useState(null)
  const [meta, setMeta] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const revision = useRef(null)

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const response = await fetch('/api/data', { headers: { Accept: 'application/json' } })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      setPayload(await response.json())
      setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadData() }, [loadData])

  useEffect(() => {
    let cancelled = false

    const poll = async () => {
      try {
        const response = await fetch('/api/meta')
        if (!response.ok) return
        const next = await response.json()
        if (cancelled) return
        setMeta(next)
        setError(null)
        if (revision.current !== null && next.revision !== revision.current) loadData()
        revision.current = next.revision
      } catch (err) {
        if (!cancelled) setError(err.message)
      }
    }

    poll()
    const timer = setInterval(poll, POLL_MS)
    return () => { cancelled = true; clearInterval(timer) }
  }, [loadData])

  return { payload, meta, error, loading, reload: loadData }
}
