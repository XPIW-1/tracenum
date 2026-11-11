import { Fragment } from 'react'

export const normNum = (v) => String(v).replace(/[,，\s]/g, '').replace(/^[-(（]+|[)）]+$/g, '')

// Hand-drawn audit tick, the mark an auditor leaves next to a traced figure.
export function TickMark({ className = 'tickmark' }) {
  return (
    <svg className={className} viewBox="0 0 16 16" aria-hidden="true">
      <path d="M2.5 8.8 L6.2 12.2 L13.8 3.6" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function cleanLatex(s) {
  return s
    .replace(/\\text\{([^}]*)\}/g, '$1')
    .replace(/\\times/g, '×').replace(/\\div/g, '÷').replace(/\\approx/g, '≈')
    .replace(/\\left|\\right/g, '').replace(/\\%/g, '%')
    .replace(/\\frac\{([^}]*)\}\{([^}]*)\}/g, '($1) / ($2)')
    .replace(/\\[,;!]|\\ /g, ' ').replace(/\\quad/g, '  ')
    .replace(/[{}]/g, '').replace(/\s+/g, ' ').trim()
}

const INLINE = /(\*\*[^*]+\*\*)|(\[?第\s*\d+\s*页\]?)|(\\\((.+?)\\\))|(-?\d+(?:\.\d+)?%|-?\(?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?|-?\d+\.\d+|-?\d{3,}(?:\.\d+)?)/g

function Inline({ text, numbers, onPage, onFigure }) {
  const parts = []
  let last = 0
  let m
  INLINE.lastIndex = 0
  while ((m = INLINE.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index))
    const key = m.index
    if (m[1]) {
      parts.push(<strong key={key}><Inline text={m[1].slice(2, -2)} numbers={numbers} onPage={onPage} onFigure={onFigure} /></strong>)
    } else if (m[2]) {
      const page = Number(m[2].match(/\d+/)[0])
      parts.push(<button key={key} type="button" className="page-link" onClick={() => onPage(page)} title={`打开年报第 ${page} 页`}>第{page}页</button>)
    } else if (m[3]) {
      parts.push(<Fragment key={key}>{cleanLatex(m[4])}</Fragment>)
    } else if (m[5]) {
      const raw = m[5]
      const info = numbers.get(normNum(raw))
      if (!info) parts.push(raw)
      else if (info.status === 'matched')
        parts.push(
          <button key={key} type="button" className="figure" onClick={() => onFigure(info)} title={`原文第 ${info.pages.join('、')} 页逐字出现，点击查看`}>
            {raw}<TickMark />
          </button>,
        )
      else parts.push(<span key={key} className="figure derived" title="由原文数值计算得出，原文中不直接出现">{raw}</span>)
    }
    last = m.index + m[0].length
  }
  if (last < text.length) parts.push(text.slice(last))
  return <>{parts}</>
}

export default function AnswerText({ text, numbers = [], onPage, onFigure }) {
  const map = new Map(numbers.map((n) => [normNum(n.value), n]))
  const blocks = []
  const source = (text || '').replace(/\r/g, '')
  // Split out display math first.
  const segments = source.split(/\\\[([\s\S]*?)\\\]/g)
  segments.forEach((seg, i) => {
    if (i % 2 === 1) {
      blocks.push({ type: 'calc', text: cleanLatex(seg) })
      return
    }
    let list = null
    for (const rawLine of seg.split('\n')) {
      const line = rawLine.trim()
      if (!line) { list = null; continue }
      if (/^[-*•]\s+/.test(line)) {
        if (!list) { list = { type: 'list', items: [] }; blocks.push(list) }
        list.items.push(line.replace(/^[-*•]\s+/, ''))
        continue
      }
      list = null
      if (/^#{1,4}\s+/.test(line)) blocks.push({ type: 'h', text: line.replace(/^#+\s+/, '') })
      else if (/^\*\*[^*]+\*\*[:：]?$/.test(line)) blocks.push({ type: 'h', text: line.replace(/\*\*/g, '') })
      else blocks.push({ type: 'p', text: line })
    }
  })
  const props = { numbers: map, onPage, onFigure }
  return (
    <div className="answer">
      {blocks.map((b, i) => {
        if (b.type === 'calc') return <div key={i} className="calc"><Inline text={b.text} {...props} /></div>
        if (b.type === 'h') return <h4 key={i}><Inline text={b.text} {...props} /></h4>
        if (b.type === 'list') return <ul key={i}>{b.items.map((it, j) => <li key={j}><Inline text={it} {...props} /></li>)}</ul>
        return <p key={i}><Inline text={b.text} {...props} /></p>
      })}
    </div>
  )
}
