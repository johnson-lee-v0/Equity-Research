const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/
const MONTH_ONLY = /^(\d{4})-(\d{2})$/

function valueText(value: unknown) {
  if (value == null) return null
  const next = String(value).trim()
  return next || null
}

function dateOnlyValue(value: string) {
  const match = DATE_ONLY.exec(value)
  if (!match) return null
  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  const date = new Date(year, month - 1, day, 12, 0, 0, 0)
  if (
    date.getFullYear() !== year ||
    date.getMonth() !== month - 1 ||
    date.getDate() !== day
  ) return null
  return date
}

function monthOnlyValue(value: string) {
  const match = MONTH_ONLY.exec(value)
  if (!match) return null
  const year = Number(match[1])
  const month = Number(match[2])
  if (month < 1 || month > 12) return null
  return new Date(year, month - 1, 1, 12, 0, 0, 0)
}

const calendarOptions: Intl.DateTimeFormatOptions = {
  month: 'short',
  day: 'numeric',
  year: 'numeric',
}

const monthOptions: Intl.DateTimeFormatOptions = {
  month: 'short',
  year: 'numeric',
}

export function formatCalendarDate(value: unknown, fallback = 'Unknown') {
  const next = valueText(value)
  if (!next) return fallback
  const monthOnly = monthOnlyValue(next)
  if (monthOnly) return new Intl.DateTimeFormat(undefined, monthOptions).format(monthOnly)
  const date = dateOnlyValue(next) ?? new Date(next)
  if (Number.isNaN(date.getTime())) return next
  return new Intl.DateTimeFormat(undefined, calendarOptions).format(date)
}

export function formatDateTime(value: unknown, fallback = 'Unknown', timeZoneName?: 'short' | 'long') {
  const next = valueText(value)
  if (!next) return fallback

  const monthOnly = monthOnlyValue(next)
  if (monthOnly) return new Intl.DateTimeFormat(undefined, monthOptions).format(monthOnly)

  // A period such as 2026-09-11 names a calendar day, not midnight UTC.
  // Format it without a time or timezone so the displayed day cannot shift
  // across the user's local timezone.
  const dateOnly = dateOnlyValue(next)
  if (dateOnly) return new Intl.DateTimeFormat(undefined, calendarOptions).format(dateOnly)

  const date = new Date(next)
  if (Number.isNaN(date.getTime())) return next
  const options: Intl.DateTimeFormatOptions = {
    ...calendarOptions,
    hour: 'numeric',
    minute: '2-digit',
  }
  if (timeZoneName) options.timeZoneName = timeZoneName
  return new Intl.DateTimeFormat(undefined, options).format(date)
}
