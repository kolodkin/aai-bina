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
  postgres: {
    type: 'postgres',
    label: 'Postgres',
    formTestid: 'postgres-form',
    testTestid: 'pg-test',
    connectTestid: 'pg-connect',
    resultTestid: 'pg-result',
    fields: [
      { key: 'name', label: 'Name', testid: 'pg-name', type: 'text', default: 'postgres' },
      { key: 'host', label: 'Host', testid: 'pg-host', type: 'text', default: 'localhost' },
      { key: 'port', label: 'Port', testid: 'pg-port', type: 'text', default: '5432' },
      { key: 'username', label: 'Username', testid: 'pg-username', type: 'text', default: 'postgres' },
      { key: 'password', label: 'Password', testid: 'pg-password', type: 'password', default: '' },
    ],
  },
  duckdb: {
    type: 'duckdb',
    label: 'DuckDB',
    formTestid: 'duckdb-form',
    testTestid: 'duck-test',
    connectTestid: 'duck-connect',
    resultTestid: 'duck-result',
    fields: [
      { key: 'name', label: 'Name', testid: 'duck-name', type: 'text', default: 'duckdb' },
      { key: 'path', label: 'Path', testid: 'duck-path', type: 'text', default: ':memory:' },
    ],
  },
}
