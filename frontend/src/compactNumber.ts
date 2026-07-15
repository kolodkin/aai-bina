// Abbreviations for the explorer sidebar's row counts and byte sizes.
//
// Shared core: step `base` per unit, one decimal below 10 units, integer
// above; a value that rounds up to `base` carries into the next unit
// ("1M", not "1000K").
function format(n: number, base: number, units: string[]): string {
  let i = 0
  let v = n
  while (v >= base && i < units.length - 1) {
    v /= base
    i++
  }
  if (i === 0) return `${n}${units[0]}`
  const rounded = v < 10 ? Math.round(v * 10) / 10 : Math.round(v)
  if (rounded >= base && i < units.length - 1) return `1${units[i + 1]}`
  return `${rounded}${units[i]}`
}

// Counts on the metric ladder: 999 -> "999", 1234 -> "1.2K", 3400000 -> "3.4M".
export function formatCompact(n: number): string {
  return format(n, 1000, ['', 'K', 'M', 'G', 'T', 'P'])
}

// Byte sizes step at powers of 1024, labeled KB/MB/GB/TB/PB: 262144 -> "256KB".
export function formatBytes(n: number): string {
  return format(n, 1024, ['B', 'KB', 'MB', 'GB', 'TB', 'PB'])
}
