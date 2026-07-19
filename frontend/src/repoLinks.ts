// Deep links into a repo's web host, built from the credential-free browse URL
// the backend derives from the git remote. Only the three major public hosts
// are supported; any other host (including self-hosted instances) returns null
// so the UI shows plain text rather than a guessed, possibly-wrong link.

const HOSTS = {
  // blob = a file at a ref; tree = a folder at a ref. Bitbucket serves both
  // from /src.
  'github.com': { commit: '/commit/', tree: '/tree/', blob: '/blob/' },
  'gitlab.com': { commit: '/-/commit/', tree: '/-/tree/', blob: '/-/blob/' },
  'bitbucket.org': { commit: '/commits/', tree: '/src/', blob: '/src/' },
} as const

// Encode each path segment but keep the slashes, so nested refs/paths resolve.
function encodePath(p: string): string {
  return p.split('/').map(encodeURIComponent).join('/')
}

function scheme(webUrl: string | null) {
  if (!webUrl) return null
  let host: string
  try {
    host = new URL(webUrl).host
  } catch {
    return null
  }
  const s = HOSTS[host as keyof typeof HOSTS]
  return s ? { base: webUrl.replace(/\/+$/, ''), ...s } : null
}

// The commit URL for a sha, or null when the host isn't a supported one.
export function commitUrl(webUrl: string | null, sha: string): string | null {
  const s = scheme(webUrl)
  return s ? `${s.base}${s.commit}${sha}` : null
}

// The branch (tree) URL, or null when the host isn't a supported one.
export function branchUrl(webUrl: string | null, branch: string): string | null {
  const s = scheme(webUrl)
  return s ? `${s.base}${s.tree}${encodePath(branch)}` : null
}

// The URL to an entity's folder (isFolder) or file at a given ref (a sha or
// branch), or null when the host isn't a supported one.
export function entityUrl(
  webUrl: string | null,
  ref: string,
  path: string,
  isFolder: boolean,
): string | null {
  const s = scheme(webUrl)
  if (!s) return null
  return `${s.base}${isFolder ? s.tree : s.blob}${encodePath(ref)}/${encodePath(path)}`
}
