/** Used only before the provider's authoritative capability list is available. */
export function modelEfforts(model: string): string[] {
  if (['gpt-6-luna', 'gpt-5.6-luna'].includes(model)) return ['low', 'medium', 'high', 'xhigh', 'max']
  if (['gpt-6-sol', 'gpt-6-astra', 'gpt-5.6-sol', 'gpt-5.6-terra'].includes(model)) return ['low', 'medium', 'high', 'xhigh', 'max', 'ultra']
  return ['low', 'medium', 'high']
}
