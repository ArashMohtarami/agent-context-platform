import Markdown from 'react-markdown';

// No raw-HTML plugin, executable attributes, external images or relative links.
// Explicit links alone can navigate, with referrer/opener isolation.
export function safeKnowledgeUrl(value: string) {
  if (/[\u0000-\u0020\u007f]/.test(value)) return '';
  try {const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : '';}
  catch {return '';}
}
export function SafeMarkdown({children}: {children: string}) {
  return <div className="knowledge-markdown"><Markdown skipHtml allowedElements={['p', 'br', 'strong', 'em', 'code', 'pre', 'blockquote', 'ul', 'ol', 'li', 'hr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'a']} unwrapDisallowed urlTransform={safeKnowledgeUrl} components={{a: ({href, children}) => href ? <a href={href} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">{children}<span className="sr-only"> (opens a new tab)</span></a> : <span>{children}</span>}}>{children}</Markdown></div>;
}
