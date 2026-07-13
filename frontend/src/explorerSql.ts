// SQL the explorer generates for a table picked from the sidebar. The name is
// double-quote-escaped — the identifier quote ClickHouse, Postgres and DuckDB
// all accept — so an odd table name can't break out of the identifier.
export function tableSelectSql(table: string): string {
  return `SELECT * FROM "${table.replace(/"/g, '""')}"`
}
