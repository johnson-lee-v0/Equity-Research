import { useState, type ReactNode } from 'react'

export function PrivateFinancialDisclosure({ children, label = 'Reveal account values locally', description = 'These account and allocation values stay hidden until you open this section. The interface does not save this reveal.', className = '' }: { children: ReactNode; label?: string; description?: string; className?: string }) {
  const [open, setOpen] = useState(false)
  return <details className={`private-financial-disclosure${className ? ` ${className}` : ''}`} onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>{label}</summary>
    {open && <div className="private-financial-disclosure-body">
      <p className="private-financial-disclosure-note">{description}</p>
      {children}
    </div>}
  </details>
}
