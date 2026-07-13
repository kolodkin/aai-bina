// TabSeparatedWithNames parsing, shared by the query panel and the explorer.

export function parseTsv(text: string): { columns: string[]; rows: string[][] } {
  // TabSeparatedWithNames: the first line is the column names, the rest are rows.
  if (text === '') return { columns: [], rows: [] }
  const lines = text.split('\n')
  return { columns: lines[0].split('\t'), rows: lines.slice(1).map((l) => l.split('\t')) }
}
