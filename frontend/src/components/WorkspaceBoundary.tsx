import { Component, type ErrorInfo, type ReactNode } from 'react'

export class WorkspaceBoundary extends Component<
  { children: ReactNode },
  { error: string | null }
> {
  state: { error: string | null } = { error: null }

  static getDerivedStateFromError(error: unknown) {
    return {
      error: error instanceof Error ? error.message : 'This workspace could not be displayed.',
    }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Workspace failed to load', error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      return (
        <section className="connection-banner banner-stale" role="alert">
          <div>
            <strong>Workspace could not load.</strong>
            <p>Reload to use the latest local build. Your saved records are retained.</p>
            <details>
              <summary>Error details</summary>
              <p>{this.state.error}</p>
            </details>
            <button className="button button-primary" onClick={() => window.location.reload()}>
              Reload workspace
            </button>
          </div>
        </section>
      )
    }
    return this.props.children
  }
}
