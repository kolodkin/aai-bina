// Autocomplete for the command prompt. Pure: given the current input and a bit
// of context, return the completions to offer. The component owns the dropdown
// UI and keyboard handling; this module owns *what* to suggest.

export type Suggestion = {
  value: string // full input text to set when this suggestion is accepted
  label: string // the token shown in the dropdown
  hint: string // secondary description
}

// `needs` gates a command on session state: 'ready' (a database is selected, so
// queries can run) or 'connected' (any active connection). Undefined = always.
type CommandSpec = {
  word: string
  arg: boolean // takes an argument → accepting leaves a trailing space
  hint: string
  needs?: 'ready' | 'connected'
}

const COMMANDS: CommandSpec[] = [
  { word: 'new', arg: true, hint: 'create a connection' },
  { word: 'connect', arg: true, hint: 'open a saved connection' },
  { word: 'query', arg: false, hint: 'run a query', needs: 'ready' },
  { word: 'dashboard', arg: true, hint: 'open a dashboard' },
  { word: 'disconnect', arg: false, hint: 'close the connection', needs: 'connected' },
]

export type SuggestContext = {
  drivers: string[]
  connections?: string[] // saved connection names, for `connect <name>`
  ready?: boolean
  connected?: boolean
}

export function suggestCompletions(input: string, ctx: SuggestContext): Suggestion[] {
  const { drivers, connections = [], ready = false, connected = false } = ctx
  const lead = input.replace(/^\s+/, '')
  const spaceIdx = lead.indexOf(' ')

  // Still typing the first word: complete the command keyword.
  if (spaceIdx === -1) {
    const partial = lead.toLowerCase()
    return COMMANDS.filter((c) => {
      if (c.needs === 'ready' && !ready) return false
      if (c.needs === 'connected' && !connected) return false
      return c.word.startsWith(partial)
    }).map((c) => ({
      value: c.arg ? `${c.word} ` : c.word,
      label: c.word,
      hint: c.hint,
    }))
  }

  // First word complete; complete the argument for commands that take one.
  const word = lead.slice(0, spaceIdx).toLowerCase()
  const partial = lead.slice(spaceIdx + 1).trimStart().toLowerCase()
  if (word === 'new') {
    return drivers
      .filter((d) => d.toLowerCase().startsWith(partial))
      .map((d) => ({ value: `new ${d}`, label: d, hint: 'driver' }))
  }
  if (word === 'connect') {
    return connections
      .filter((n) => n.toLowerCase().startsWith(partial))
      .map((n) => ({ value: `connect ${n}`, label: n, hint: 'connection' }))
  }

  return []
}
