import { lazy, StrictMode, Suspense } from 'react'
import { createRoot } from 'react-dom/client'

// Vite removes the unused branch. The public build never boots the local API
// client or event stream, and cannot start an agent or open a personal ledger.
const App = lazy(() => __PUBLIC_DEMO__ ? import('./demo/SyntheticDemo') : import('./App'))

const root = document.getElementById('root')
if (!root) throw new Error('ResearchCouncil root element is missing')

createRoot(root).render(<StrictMode><Suspense fallback={<p role="status">Opening ResearchCouncil…</p>}><App /></Suspense></StrictMode>)
