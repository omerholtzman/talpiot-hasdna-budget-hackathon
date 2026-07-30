import path from 'node:path'
import { CONTENT_ROOT } from './contentRoot.js'

/**
 * Resolves a slug (array of URL path segments) to an absolute .md file path
 * inside CONTENT_ROOT. Returns null if the slug is empty, contains a `.`/`..`
 * segment, or resolves outside CONTENT_ROOT.
 */
export function resolveSlugPath(slugSegments) {
  if (!Array.isArray(slugSegments) || slugSegments.length === 0) return null

  const hasInvalidSegment = slugSegments.some(
    (segment) => segment === '.' || segment === '..' || segment === ''
  )
  if (hasInvalidSegment) return null

  const relativePath = slugSegments.join('/')
  const resolvedPath = path.resolve(CONTENT_ROOT, `${relativePath}.md`)

  const isInsideContentRoot =
    resolvedPath === CONTENT_ROOT ||
    resolvedPath.startsWith(CONTENT_ROOT + path.sep)

  return isInsideContentRoot ? resolvedPath : null
}
