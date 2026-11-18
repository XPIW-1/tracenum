import { useEffect, useMemo, useState } from 'react'
import { detectMode, getJSON } from './api.js'
import Lookup from './components/Lookup.jsx'
import Lab from './components/Lab.jsx'
import Method from './components/Method.jsx'
import Draft from './components/Draft.jsx'
import PdfPane from './components/PdfPane.jsx'

const VIEWS = [
  { id: 'lookup', label: '查数' },
  { id: 'lab', label: '三策略实验台' },
  { id: 'method', label: '实验设计' },
]

function loadDraft() {
  try { return JSON.parse(localStorage.getItem('tracenum.draft') || '[]') } catch { return [] }
}

export default function App() {
  const [view, setView] = useState(() => (location.hash.slice(1) in { lookup: 1, lab: 1, method: 1 } ? location.hash.slice(1) : 'lookup'))
  const [corpus, setCorpus] = useState([])
  const [questions, setQuestions] = useState([])
  const [featured, setFeatured] = useState([])
  const [mode, setMode] = useState(null)
  const [status, setStatus] = useState(null)
  const [company, setCompany] = useState('BYD')
  const [pdfTarget, setPdfTarget] = useState(null)
  const [labPdf, setLabPdf] = useState(null)
  const [draft, setDraft] = useState(loadDraft)
  const [drawer, setDrawer] = useState(false)
  const [toast, setToast] = useState(null)

  useEffect(() => {
    getJSON('/data/corpora.json').then(setCorpus).catch(() => {})
    getJSON('/data/questions.json').then(setQuestions).catch(() => {})
    getJSON('/data/showcase.json').then((showcase) => setFeatured(showcase.featured)).catch(() => {})
    detectMode().then(setMode)
  }, [])
  useEffect(() => {
    if (mode !== 'live') return
    let timer
    const controller = new AbortController()
    const poll = async () => {
      try {
        const next = await getJSON('/api/status', controller.signal)
        if (controller.signal.aborted) return
        setStatus(next)
        if (!next.ready && !next.error) timer = setTimeout(poll, 2000)
      } catch {
        if (controller.signal.aborted) return
        setStatus({ error: '后端未启动' })
        timer = setTimeout(poll, 4000)
      }
    }
    poll()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [mode])
  useEffect(() => { try { localStorage.setItem('tracenum.draft', JSON.stringify(draft)) } catch { /* private mode */ } }, [draft])
  useEffect(() => { if (location.hash.slice(1) !== view) history.pushState(null, '', `#${view}`); window.scrollTo(0, 0) }, [view])
  useEffect(() => {
    const onHash = () => { const v = location.hash.slice(1); if (VIEWS.some((x) => x.id === v)) setView(v) }
    window.addEventListener('hashchange', onHash)
    window.addEventListener('popstate', onHash)
    return () => { window.removeEventListener('hashchange', onHash); window.removeEventListener('popstate', onHash) }
  }, [])
  useEffect(() => { if (!toast) return; const id = setTimeout(() => setToast(null), 2200); return () => clearTimeout(id) }, [toast])

  const lookupTarget = useMemo(
    () => (pdfTarget && pdfTarget.company === company ? pdfTarget : { company, page: 1, values: [], cover: true }),
    [pdfTarget, company],
  )
  const draftIds = new Set(draft.map((d) => d.id))
  const addDraft = (item) => { setDraft((d) => (d.some((x) => x.id === item.id) ? d : [...d, item])); setToast(`已加入底稿：${item.value}`) }
  const statusLabel = mode === 'static' ? '公开演示 · 预计算结果' : !status ? '连接中' : status.error ? '后端不可用' : status.ready ? `${status.model} 已就绪` : '正在加载检索模型'
  const statusClass = mode === 'static' ? 'static' : status?.error ? 'error' : status?.ready ? 'ready' : ''

  return (
    <>
      <header className="topbar">
        <a className="brand" href="#lookup" onClick={(e) => { e.preventDefault(); setView('lookup') }}>
          <span className="brand-mark">溯数</span><span className="brand-latin">TraceNum</span>
        </a>
        <nav className="nav" aria-label="主导航">
          {VIEWS.map((v) => <button key={v.id} type="button" aria-current={view === v.id ? 'page' : undefined} onClick={() => setView(v.id)}>{v.label}</button>)}
        </nav>
        <div className="topbar-right">
          <span className={`status-dot ${statusClass}`} title={status?.error || ''}><i /><span className="status-text">{statusLabel}</span></span>
          <button type="button" className="draft-button" onClick={() => setDrawer(true)}>底稿 <b>{draft.length}</b></button>
        </div>
      </header>

      {view === 'lookup' && (
        <div className="lookup">
          <main className="lookup-main">
            <Lookup mode={mode} featured={featured} corpus={corpus} company={company} setCompany={setCompany} questions={questions} openPdf={setPdfTarget}
              addDraft={addDraft} draftIds={draftIds} openMethod={() => setView('method')} status={status} />
          </main>
          <aside className="lookup-side" aria-label="年报原页">
            <PdfPane target={lookupTarget} corpus={corpus} onPage={setPdfTarget} />
          </aside>
        </div>
      )}
      {view === 'lab' && (
        <>
          <Lab mode={mode} featured={featured} corpus={corpus} questions={questions} openPdf={setLabPdf} budget={status?.budget} />
          {labPdf && (
            <>
              <div className="drawer-scrim" onClick={() => setLabPdf(null)} />
              <aside className="drawer lookup-side" style={{ width: 'min(640px, 100vw)' }} role="dialog" aria-label="年报原页">
                <PdfPane target={labPdf} corpus={corpus} onPage={setLabPdf} />
                <div className="drawer-foot"><button className="btn ghost" onClick={() => setLabPdf(null)}>关闭原页</button></div>
              </aside>
            </>
          )}
        </>
      )}
      {view === 'method' && <Method mode={mode} questions={questions} goLab={() => setView('lab')} />}

      {drawer && <Draft items={draft} setItems={setDraft} onClose={() => setDrawer(false)} corpus={corpus}
        openPdf={(t) => { setDrawer(false); setView('lookup'); setCompany(t.company); setPdfTarget(t) }} toast={setToast} />}
      {toast && <div className="toast" role="status">{toast}</div>}
    </>
  )
}
