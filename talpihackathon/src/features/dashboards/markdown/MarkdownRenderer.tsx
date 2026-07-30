import { isValidElement } from 'react'
import ReactMarkdown from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { createDashboardLinkRenderer } from './DashboardLink'
import { MermaidBlock } from './MermaidBlock'
import './markdown.css'

interface MarkdownRendererProps {
  content: string
  currentSlug: string
}

function extractMermaidCode(preChildren: React.ReactNode): string | null {
  const codeElement = Array.isArray(preChildren) ? preChildren[0] : preChildren
  if (!isValidElement<{ className?: string; children?: React.ReactNode }>(codeElement)) {
    return null
  }

  const className = codeElement.props.className ?? ''
  if (!className.includes('language-mermaid')) return null

  return String(codeElement.props.children).replace(/\n$/, '')
}

export function MarkdownRenderer({ content, currentSlug }: MarkdownRendererProps) {
  const components: Components = {
    a: createDashboardLinkRenderer(currentSlug),
    pre({ children, ...rest }) {
      const mermaidCode = extractMermaidCode(children)
      if (mermaidCode !== null) return <MermaidBlock code={mermaidCode} />
      return <pre {...rest}>{children}</pre>
    },
  }

  return (
    <div className="markdown-body">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  )
}
