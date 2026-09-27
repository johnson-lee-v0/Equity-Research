export type PrimarySection = 'research' | 'watchlist' | 'portfolio' | 'congress' | 'strategies' | 'memory' | 'office' | 'settings'
export type ResearchSection = 'cases' | 'earnings' | 'history'

/** Old bookmarks retain their content inside the current navigation. */
export function navigationFor(hash: string): { primary: PrimarySection; research: ResearchSection; origin: string; workspace: 'research' | 'decisions' | 'portfolio' | 'memory' | 'office' | 'settings' } {
  const [page, tab] = hash.replace(/^#/, '').split('/')
  if (page === 'watchlist' || page === 'coverage') return {primary: 'watchlist', research: 'cases', origin: 'all', workspace: 'decisions'}
  if (page === 'portfolio' || page === 'memory' || page === 'office' || page === 'settings') return {primary: page, research: 'cases', origin: 'all', workspace: page}
  if (page === 'congress') return {primary: 'congress', research: 'cases', origin: 'all', workspace: 'research'}
  if (['strategies', 'simulation', 'scenarios'].includes(page)) return {primary:'strategies', research:'cases',origin:'all',workspace:'research'}
  return {primary: 'research', research: page === 'documents' || tab === 'earnings' ? 'earnings' : page === 'library' || tab === 'history' ? 'history' : 'cases', origin: ['reddit','inbox'].includes(page) ? 'reddit' : 'all', workspace: 'research'}
}
