import { Link } from 'react-router'
import type { Components } from 'react-markdown'
import { isExternalLink, resolveRelativeContentLink } from './linkResolution'

export function createDashboardLinkRenderer(currentSlug: string): Components['a'] {
  return function DashboardLink({ href, children, ...rest }) {
    if (!href) return <a {...rest}>{children}</a>

    if (isExternalLink(href)) {
      return (
        <a {...rest} href={href} target="_blank" rel="noopener noreferrer">
          {children}
        </a>
      )
    }

    const resolvedSlug = resolveRelativeContentLink(currentSlug, href)
    if (resolvedSlug) {
      return (
        <Link {...rest} to={`/d/${resolvedSlug}`}>
          {children}
        </Link>
      )
    }

    return (
      <a {...rest} href={href}>
        {children}
      </a>
    )
  }
}
