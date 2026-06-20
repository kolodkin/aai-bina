// Frontend driver registry: drives the `new <type>` command and the connection
// form. Plans 2 and 3 append a postgres / duckdb entry — no other UI changes.
export type DriverField = {
  key: string
  label: string
  testid: string
  type: 'text' | 'password'
  default: string
}

export type DriverMeta = {
  type: string
  label: string
  fields: DriverField[]
  formTestid: string
  testTestid: string
  connectTestid: string
  resultTestid: string
}

export const DRIVERS: Record<string, DriverMeta> = {
  clickhouse: {
    type: 'clickhouse',
    label: 'ClickHouse',
    formTestid: 'clickhouse-form',
    testTestid: 'ch-test',
    connectTestid: 'ch-connect',
    resultTestid: 'ch-result',
    fields: [
      { key: 'name', label: 'Name', testid: 'ch-name', type: 'text', default: 'clickhouse' },
      { key: 'host', label: 'Host', testid: 'ch-host', type: 'text', default: 'localhost' },
      { key: 'port', label: 'Port', testid: 'ch-port', type: 'text', default: '8123' },
      { key: 'username', label: 'Username', testid: 'ch-username', type: 'text', default: 'default' },
      { key: 'password', label: 'Password', testid: 'ch-password', type: 'password', default: '' },
    ],
  },
}
