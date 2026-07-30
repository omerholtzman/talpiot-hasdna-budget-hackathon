const EXTERNAL_LINK_PATTERN = /^(https?:|mailto:|tel:)/i

export function isExternalLink(href: string): boolean {
  return EXTERNAL_LINK_PATTERN.test(href) || href.startsWith('//')
}

function stripQueryAndHash(href: string): string {
  return href.split(/[?#]/)[0] ?? ''
}

/**
 * Resolves a markdown link's href against the slug of the dashboard it
 * appears in, returning the target dashboard slug (no .md extension) or
 * null if the link isn't a relative .md link or resolves outside content/.
 *
 * This is a UX convenience only — the server independently enforces the
 * content-root boundary when the resulting slug is fetched.
 */
export function resolveRelativeContentLink(currentSlug: string, href: string): string | null {
  if (isExternalLink(href)) return null

  const pathPart = stripQueryAndHash(href)
  if (!pathPart.endsWith('.md')) return null

  const isAbsolute = pathPart.startsWith('/')
  const lastSlashIndex = currentSlug.lastIndexOf('/')
  const currentDir = lastSlashIndex === -1 ? '' : currentSlug.slice(0, lastSlashIndex)

  const segments = isAbsolute ? [] : currentDir ? currentDir.split('/') : []

  for (const segment of pathPart.split('/')) {
    if (segment === '' || segment === '.') continue
    if (segment === '..') {
      if (segments.length === 0) return null
      segments.pop()
      continue
    }
    segments.push(segment)
  }

  if (segments.length === 0) return null

  const joined = segments.join('/')
  return joined.slice(0, -'.md'.length)
}
