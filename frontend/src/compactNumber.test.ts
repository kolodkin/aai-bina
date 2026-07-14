import { expect, test } from 'vitest'
import { formatBytes, formatCompact } from './compactNumber'

test('below 1000 stays verbatim', () => {
  expect(formatCompact(0)).toBe('0')
  expect(formatCompact(3)).toBe('3')
  expect(formatCompact(999)).toBe('999')
})

test('thousands get K with one decimal below 10', () => {
  expect(formatCompact(1000)).toBe('1K')
  expect(formatCompact(1234)).toBe('1.2K')
  expect(formatCompact(9949)).toBe('9.9K')
  expect(formatCompact(12345)).toBe('12K')
  expect(formatCompact(999499)).toBe('999K')
})

test('each power of 1000 steps a suffix: K M G T P', () => {
  expect(formatCompact(3_400_000)).toBe('3.4M')
  expect(formatCompact(5_000_000_000)).toBe('5G')
  expect(formatCompact(7_200_000_000_000)).toBe('7.2T')
  expect(formatCompact(1_000_000_000_000_000)).toBe('1P')
})

test('rounding up to 1000 carries into the next suffix', () => {
  expect(formatCompact(999_999)).toBe('1M')
  expect(formatCompact(999_950_000)).toBe('1G')
})

test('bytes step units at powers of 1024: B KB MB GB TB', () => {
  expect(formatBytes(0)).toBe('0B')
  expect(formatBytes(512)).toBe('512B')
  expect(formatBytes(1024)).toBe('1KB')
  expect(formatBytes(262144)).toBe('256KB')
  expect(formatBytes(1536)).toBe('1.5KB')
  expect(formatBytes(26 * 1024 * 1024)).toBe('26MB')
  expect(formatBytes(5.5 * 1024 ** 3)).toBe('5.5GB')
  expect(formatBytes(3 * 1024 ** 4)).toBe('3TB')
})

test('bytes rounding up to 1024 carries into the next unit', () => {
  expect(formatBytes(1024 * 1024 - 1)).toBe('1MB')
})
