// Abbreviate a count with metric suffixes: 999 -> "999", 1234 -> "1.2K",
// 3400000 -> "3.4M", then G/T/P. One decimal below 10 units, integer above;
// a value that rounds up to 1000 carries into the next suffix ("1M", not
// "1000K"). Used for the explorer sidebar's row counts and byte sizes.
export function formatCompact(n: number): string {
  const units = ['', 'K', 'M', 'G', 'T', 'P']
  let i = 0
  let v = n
  while (v >= 1000 && i < units.length - 1) {
    v /= 1000
    i++
  }
  if (i === 0) return String(n)
  const rounded = v < 10 ? Math.round(v * 10) / 10 : Math.round(v)
  if (rounded >= 1000 && i < units.length - 1) return `1${units[i + 1]}`
  return `${rounded}${units[i]}`
}
