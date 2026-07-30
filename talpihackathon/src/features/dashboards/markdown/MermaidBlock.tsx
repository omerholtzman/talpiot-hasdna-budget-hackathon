import { useEffect, useId, useState } from 'react'
import mermaid from 'mermaid'

const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches

mermaid.initialize({
  startOnLoad: false,
  theme: prefersDark ? 'dark' : 'default',
  securityLevel: 'strict',
})

interface MermaidBlockProps {
  code: string
}

export function MermaidBlock({ code }: MermaidBlockProps) {
  const rawId = useId()
  const diagramId = `mermaid-${rawId.replace(/:/g, '')}`
  const [svg, setSvg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    mermaid
      .render(diagramId, code)
      .then((result) => {
        if (!cancelled) setSvg(result.svg)
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to render diagram')
        }
      })

    return () => {
      cancelled = true
    }
  }, [code, diagramId])

  if (error) {
    return <pre className="mermaid-error">Failed to render diagram: {error}</pre>
  }

  if (!svg) {
    return <div className="mermaid-placeholder" aria-hidden="true" />
  }

  // mermaid's 'strict' securityLevel sanitizes the SVG it returns.
  return <div className="mermaid-diagram" dangerouslySetInnerHTML={{ __html: svg }} />
}
