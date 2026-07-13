import { expect, test } from 'vitest'
import { tableSelectSql } from './explorerSql'

test('wraps the table name in double quotes', () => {
  expect(tableSelectSql('items')).toBe('SELECT * FROM "items"')
})

test('doubles embedded quotes so the name cannot escape the identifier', () => {
  expect(tableSelectSql('we"ird')).toBe('SELECT * FROM "we""ird"')
})
