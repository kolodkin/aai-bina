// Deep links into a repo's web host, built from the credential-free browse URL
// the backend derives from the git remote. Only the three major public hosts
// are supported; any other host (including self-hosted instances) returns null
// so the UI shows plain text rather than a guessed, possibly-wrong link.

const HOSTS = {
  'github.com': { commit: '/commit/', tree: '/tree/' },
  'gitlab.com': { commit: '/-/commit/', tree: '/-/tree/' },
  'bitbucket.org': { commit: '/commits/', tree: '/src/' },
} as const

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

// The branch (tree) URL, or null when the host isn't a supported one. Branch
// segments are encoded but slashes are kept so nested names (feature/x) resolve.
export function branchUrl(webUrl: string | null, branch: string): string | null {
  const s = scheme(webUrl)
  if (!s) return null
  const encoded = branch.split('/').map(encodeURIComponent).join('/')
  return `${s.base}${s.tree}${encoded}`
}
