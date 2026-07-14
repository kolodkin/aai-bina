import { expect, test } from 'vitest'
import { formatCompact } from './compactNumber'

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
