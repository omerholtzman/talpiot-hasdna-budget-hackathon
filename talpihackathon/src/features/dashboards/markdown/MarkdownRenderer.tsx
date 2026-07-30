import { isValidElement } from 'react'
import type { ComponentPropsWithoutRef, ElementType } from 'react'
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

// react-markdown renders each block-level element independently, so giving
// every text block its own dir="auto" lets the browser resolve Hebrew/Arabic
// (RTL) vs. Latin (LTR) direction per block, based on its own first
// strong-directionality character, instead of one fixed direction for the
// whole document.
function withAutoDirection<T extends ElementType>(tag: T) {
  const Tag = tag as ElementType
  return function AutoDirElement(props: ComponentPropsWithoutRef<T> & { node?: unknown }) {
    const { node: _node, ...rest } = props
    return <Tag dir="auto" {...rest} />
  }
}

export function MarkdownRenderer({ content, currentSlug }: MarkdownRendererProps) {
  const components: Components = {
    a: createDashboardLinkRenderer(currentSlug),
    p: withAutoDirection('p'),
    li: withAutoDirection('li'),
    h1: withAutoDirection('h1'),
    h2: withAutoDirection('h2'),
    h3: withAutoDirection('h3'),
    h4: withAutoDirection('h4'),
    blockquote: withAutoDirection('blockquote'),
    td: withAutoDirection('td'),
    th: withAutoDirection('th'),
    pre({ children, ...rest }) {
      const mermaidCode = extractMermaidCode(children)
      if (mermaidCode !== null) return <MermaidBlock code={mermaidCode} />
      // Code stays left-to-right regardless of surrounding text direction.
      return (
        <pre dir="ltr" {...rest}>
          {children}
        </pre>
      )
    },
  }

  return (
    <div className="markdown-body" dir="auto">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  )
}
