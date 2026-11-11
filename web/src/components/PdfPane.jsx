import { useEffect, useRef, useState } from 'react'
import * as pdfjs from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { ChevronLeft, ChevronRight, FileSearch } from 'lucide-react'
import { normNum, TickMark } from './AnswerText.jsx'

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl
const docs = new Map()
function loadDoc(code) {
  if (!docs.has(code)) docs.set(code, pdfjs.getDocument(`/api/pdf/${code}`).promise)
  return docs.get(code)
}

// target: { company, page, values: [raw strings], label }
export default function PdfPane({ target, corpus, onPage }) {
  const scrollRef = useRef(null)
  const canvasRef = useRef(null)
  const [size, setSize] = useState(null)
  const [marks, setMarks] = useState([])
  const [total, setTotal] = useState(null)
  const [state, setState] = useState('idle')
  const [renderKey, setRenderKey] = useState(0)

  useEffect(() => {
    if (!target) return
    let cancelled = false
    let task
    setState('loading')
    ;(async () => {
      try {
        const pdf = await loadDoc(target.company)
        if (cancelled) return
        setTotal(pdf.numPages)
        const page = await pdf.getPage(Math.min(Math.max(1, target.page), pdf.numPages))
        const width = Math.max(300, (scrollRef.current?.clientWidth ?? 640) - 56)
        const base = page.getViewport({ scale: 1 })
        const viewport = page.getViewport({ scale: width / base.width })
        const ratio = window.devicePixelRatio || 1
        const canvas = canvasRef.current
        canvas.width = Math.floor(viewport.width * ratio)
        canvas.height = Math.floor(viewport.height * ratio)
        canvas.style.width = `${viewport.width}px`
        canvas.style.height = `${viewport.height}px`
        const ctx = canvas.getContext('2d')
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0)
        task = page.render({ canvasContext: ctx, viewport })
        await task.promise
        if (cancelled) return
        setSize({ w: viewport.width, h: viewport.height })
        const wanted = (target.values || []).map(normNum).filter((v) => v.replace('.', '').length >= 3)
        const found = []
        if (wanted.length) {
          const content = await page.getTextContent()
          for (const item of content.items) {
            const s = normNum(item.str || '')
            if (!s || s.length < 3) continue
            if (wanted.some((w) => s.includes(w) || (w.length >= 5 && w.includes(s) && s.length >= 5))) {
              const t = pdfjs.Util.transform(viewport.transform, item.transform)
              const h = Math.hypot(t[2], t[3])
              found.push({ x: t[4] - 2, y: t[5] - h - 1, w: item.width * viewport.scale + 4, h: h + 4 })
            }
          }
        }
        if (cancelled) return
        setMarks(found)
        setRenderKey((k) => k + 1)
        setState('done')
        if (found.length && scrollRef.current) {
          const top = Math.min(...found.map((f) => f.y))
          scrollRef.current.scrollTo({ top: Math.max(0, top - 160), behavior: 'smooth' })
        } else scrollRef.current?.scrollTo({ top: 0 })
      } catch (err) {
        if (!cancelled && err?.name !== 'RenderingCancelledException') setState('error')
      }
    })()
    return () => { cancelled = true; task?.cancel?.() }
  }, [target])

  if (!target) {
    return (
      <div className="pdf-empty">
        <FileSearch size={28} />
        <p>答案里的每个页码和带勾的数字都可以点开。年报原页会在这里打开，相关数字用荧光笔标出。</p>
      </div>
    )
  }
  const info = corpus?.find((c) => c.code === target.company)
  return (
    <>
      <div className="pdf-head">
        <div>
          <b>{info?.company ?? target.company} {info?.year} 年年报</b>
          <div><small className="num">第 {target.page} 页{total ? ` / 共 ${total} 页` : ''}</small></div>
        </div>
        <span className="spacer" />
        <button className="icon-btn" aria-label="上一页" disabled={target.page <= 1} onClick={() => onPage({ ...target, page: target.page - 1, values: [] })}><ChevronLeft size={16} /></button>
        <button className="icon-btn" aria-label="下一页" disabled={total && target.page >= total} onClick={() => onPage({ ...target, page: target.page + 1, values: [] })}><ChevronRight size={16} /></button>
      </div>
      <div className="pdf-scroll" ref={scrollRef}>
        <div className="pdf-page" style={size ? { width: size.w, height: size.h } : undefined}>
          <canvas ref={canvasRef} />
          {marks.map((m, i) => (
            <span key={`${renderKey}-${i}`} className="hl" style={{ left: m.x, top: m.y, width: m.w, height: m.h, animationDelay: `${i * 90}ms` }} />
          ))}
        </div>
      </div>
      <div className="pdf-note" aria-live="polite">
        {state === 'loading' && '正在打开原页…'}
        {state === 'error' && '这一页没能打开，请检查后端是否在运行。'}
        {state === 'done' && target.values?.length > 0 && marks.length > 0 && (
          <span><span className="ok"><TickMark className="tickmark" /> 原文一致</span>：{target.values.join('、')} 在本页逐字出现，已用荧光笔标出。</span>
        )}
        {state === 'done' && target.values?.length > 0 && marks.length === 0 && (
          <span>在本页的文字层里没有定位到 {target.values.join('、')}（可能位于扫描图片或跨行表格中），请对照原页核对。</span>
        )}
        {state === 'done' && !target.values?.length && (target.cover
          ? '提问之后，答案引用的原页会在这里打开，相关数字用荧光笔标出。'
          : '这是答案引用的原页。点答案中带勾的数字可以直接定位到具体数值。')}
      </div>
    </>
  )
}
