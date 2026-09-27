/** Share an in-flight detail read and cancel it when another case is selected. */
export function createRunDetailRequest<T>(timeoutMs = 30_000) {
  let active: { key: string; controller: AbortController; promise: Promise<T> } | null = null

  function cancel() {
    const previous = active
    active = null
    previous?.controller.abort()
  }

  function load(runId: string, namespace: string, fetchDetail: (signal: AbortSignal) => Promise<T>): Promise<T> {
    const key = JSON.stringify([namespace, runId])
    if (active?.key === key) return active.promise
    cancel()
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(new Error('Opening this case took too long. Please try again.')), timeoutMs)
    const promise = Promise.resolve().then(() => fetchDetail(controller.signal)).finally(() => {
      clearTimeout(timer)
      if (active?.controller === controller) active = null
    })
    active = { key, controller, promise }
    return promise
  }

  return { load, cancel }
}

export type RunDetailLoadState = {
  runId: string
  status: 'loading' | 'error'
  error?: string
} | null
