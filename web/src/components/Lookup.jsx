import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowUp, Plus, Check, Square } from 'lucide-react'
import { replayStrategy, streamAsk, emptyRun, reduceRun, NODE_LABEL } from '../api.js'
import AnswerText, { normNum, TickMark } from './AnswerText.jsx'
import { FEATURED_NOTES } from '../site.js'

// Exact question_type values from the frozen question set.
const TYPE_ORDER = ['基础财务事实', '相似指标与财务口径', '多证据/计算', '跨页/分散证据', '语料边界/拒答']
const FREE_EXAMPLES = {
  BYD: ['2025年合并口径下的营业收入是多少？', '2025年研发投入金额是多少，占营业收入的比例是多少？'],
  WSI: ['2025年归属于上市公司股东的净利润是多少？', '2025年经营活动产生的现金流量净额是多少？'],
}
const SHANGHAI_TIME = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
})

export function useElapsed(running, startedAt) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!running) return
    const id = setInterval(() => setNow(Date.now()), 500)
    return () => clearInterval(id)
  }, [running])
  return startedAt ? Math.max(0, (now - startedAt) / 1000) : 0
}

export function PaperVerdict({ paper, onOpenMethod }) {
  if (!paper) return null
  const r = paper.results || {}
  return (
    <span className="paper-tag">
      论文正式题 {paper.question_id} · 盲评
      {['S1', 'S2', 'S3'].map((s) => r[s] && (
        <span key={s}>{s}<span className={r[s].correct ? 'mark-ok' : 'mark-bad'}>{r[s].correct ? '✓' : '✗'}</span></span>
      ))}
      {onOpenMethod && <button type="button" onClick={onOpenMethod}>实验设计</button>}
    </span>
  )
}

export function RoundTrace({ run, compact = false }) {
  const rows = [...run.rounds].sort((a, b) => a.round - b.round)
  const active = run.active
  if (active && !rows.find((r) => r.round === active.round)) rows.push({ round: active.round })
  return (
    <div className="trace">
      {rows.map((row) => (
        <div className="trace-round" key={row.round}>
          <b>第 {row.round} 轮</b>
          {['requery', 'retrieve', 'generate', 'check', 'decide'].map((node) => {
            const e = row[node]
            const running = active && active.round === row.round && active.node === node
            if (!e && !running) return null
            return (
              <div className={`trace-step ${running ? 'running' : ''}`} key={node}>
                <span className="label">{NODE_LABEL[node]}</span>
                <span className="detail">{e ? describe(node, e, compact) : running ? runningText(node, active) : ''}</span>
                <span className="time">{e ? `${e.latency.toFixed(1)}s` : ''}</span>
              </div>
            )
          })}
        </div>
      ))}
    </div>
  )
}

function runningText(node, active) {
  if (node === 'retrieve') {
    if (!active.total) return '在年报片段中做向量检索…'
    return <>交叉编码器逐一给候选片段打分 <span className="num">{active.done}/{active.total}</span></>
  }
  if (node === 'generate') return '模型根据证据作答…'
  if (node === 'requery') return '根据缺失项改写检索词…'
  return '进行中…'
}

const FLAG_NAME = { retrieval_complete: '证据', calculation_ok: '计算', time_match: '年度', metric_match: '口径' }
function describe(node, e, compact) {
  if (node === 'retrieve') {
    const pages = [...new Set(e.selected_pages || [])]
    return <>从 {e.candidate_count} 个候选片段中选出第 {pages.join('、')} 页{e.new_evidence != null && <>，新增 {e.new_evidence} 条证据</>}</>
  }
  if (node === 'generate') return <>累计消耗 <span className="num">{(e.total_tokens || 0).toLocaleString()}</span> tokens</>
  if (node === 'check') {
    const f = e.feedback || {}
    return <>{Object.entries(FLAG_NAME).map(([k, label]) => <span key={k} className={`flag ${f[k] ? 'ok' : 'bad'}`}>{label}{f[k] ? '通过' : '未通过'}</span>)}</>
  }
  if (node === 'decide') return <>{e.stop_reason === '继续检索' ? '核验未全部通过，继续检索' : `停止：${e.stop_reason}`}</>
  if (node === 'requery') return compact ? <>改写检索词</> : <>改写为「{e.query}」</>
  return null
}

function snippetFor(result, value, page) {
  const target = normNum(value)
  for (const ev of result.evidence || []) {
    if (ev.page !== page) continue
    const text = ev.text.replace(/\n/g, ' ')
    const flat = text.replace(/[,，]/g, '')
    const idx = flat.indexOf(target)
    if (idx >= 0) return flat.slice(Math.max(0, idx - 48), idx + target.length + 24).trim()
  }
  return ''
}

function CorpusSelector({ corpus, v3Corpora, v3Featured, corpusKey, v3Report, chooseCorpus }) {
  const companies = [...new Map(v3Corpora.map((c) => [c.code, c.company]))]
  const reports = v3Report ? v3Corpora.filter((c) => c.code === v3Report.code) : []
  // Open the extended corpus on a report that has a featured replay, so the first view is not empty.
  const featuredKeys = new Set((v3Featured || []).map((q) => q.company))  // featured entries carry the report key in `company`
  const defaultV3 = v3Corpora.find((c) => featuredKeys.has(c.key)) ?? v3Corpora.find((c) => c.report_year === 2025) ?? v3Corpora[0]
  return (
    <div className="corpus-selector">
      <div className="segmented" role="group" aria-label="语料类别">
        <button type="button" aria-pressed={!v3Report} onClick={() => v3Report && chooseCorpus('BYD')}>论文实验语料</button>
        <button type="button" aria-pressed={!!v3Report} disabled={!defaultV3} onClick={() => !v3Report && chooseCorpus(defaultV3.key)}>扩展语料 · 12 家 × 2023–2025</button>
      </div>
      {v3Report ? (
        <div className="corpus-controls">
          <select aria-label="选择公司" value={v3Report.code} onChange={(e) => {
            const own = v3Corpora.filter((c) => c.code === e.target.value)
            chooseCorpus((own.find((c) => c.report_year === v3Report.report_year) ?? own.at(-1)).key)
          }}>
            {companies.map(([code, name]) => <option key={code} value={code}>{name} · {code}</option>)}
          </select>
          <div className="segmented" role="group" aria-label="年报年度">
            {reports.map((c) => (
              <button type="button" key={c.key} aria-pressed={c.key === corpusKey} onClick={() => chooseCorpus(c.key)}>{c.report_year}</button>
            ))}
          </div>
        </div>
      ) : (
        <div className="corpus-switch" role="group" aria-label="选择年报">
          {corpus.map((c) => (
            <button key={c.key} type="button" aria-pressed={c.key === corpusKey} onClick={() => chooseCorpus(c.key)}>
              <span>{c.company} {c.report_year} 年报</span>
              <small className="num">{c.ticker} · {c.pages} 页</small>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export default function Lookup({ mode, featured, v3Featured, corpus, v3Corpora, corpusKey, setCorpusKey, questions, openPdf, addDraft, draftIds, openMethod, status }) {
  const [input, setInput] = useState('')
  const [run, setRun] = useState(null)
  const [meta, setMeta] = useState(null)
  const [error, setError] = useState(null)
  const [recordedAt, setRecordedAt] = useState(null)
  const [comparative, setComparative] = useState(false)
  const abortRef = useRef(null)
  const elapsed = useElapsed(run?.status === 'running', run?.startedAt)
  const isStatic = mode === 'static'
  const isLive = mode === 'live'
  const v3Report = v3Corpora.find((c) => c.key === corpusKey)
  const info = v3Report ?? corpus.find((c) => c.key === corpusKey)
  const ownFeatured = (v3Report ? v3Featured : featured).filter((q) => q.company === corpusKey)
  const dataYear = v3Report && v3Report.report_year - Number(comparative)

  useEffect(() => () => abortRef.current?.abort(), [])
  // Draft links can switch the report from outside this component.
  const runCorpusRef = useRef(null)
  useEffect(() => {
    setComparative(false)
    if (runCorpusRef.current && runCorpusRef.current !== corpusKey) {
      abortRef.current?.abort()
      runCorpusRef.current = null
      setRun(null)
      setMeta(null)
      setError(null)
      setRecordedAt(null)
    }
  }, [corpusKey])

  const examples = useMemo(() => {
    const own = questions.filter((q) => q.company === corpusKey)
    const groups = TYPE_ORDER.map((type) => ({ type, items: own.filter((q) => q.question_type === type).slice(0, 2) })).filter((g) => g.items.length)
    return groups
  }, [questions, corpusKey])

  async function ask(text, recording) {
    if (!recording && (!isLive || !info)) return
    const q = (recording?.question ?? text ?? input).trim()
    if (!q) return
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    setInput(q)
    setError(null)
    setMeta(null)
    setRecordedAt(recording?.recorded_at ?? null)
    runCorpusRef.current = corpusKey
    let current = emptyRun('S3')
    setRun(current)
    const onEvent = (event) => {
      if (controller.signal.aborted) return
      if (event.type === 'meta') setMeta(event)
      else if (event.strategy === 'S3') {
        current = reduceRun(current, event)
        setRun(current)
        if (event.type === 'final' && event.result.cited_pages?.length) {
          const first = event.result.numbers?.find((n) => n.status === 'matched')
          openPdf(first ? { corpusKey, page: first.pages[0], values: [first.value] } : { corpusKey, page: event.result.cited_pages[0], values: [] })
        }
      }
    }
    try {
      if (recording) await replayStrategy(recording.recording_id, 'S3', 20, onEvent, controller.signal, !!v3Report)
      else {
        const selection = v3Report ? { corpus: corpusKey } : { company: corpusKey }
        if (v3Report && comparative) selection.data_year = dataYear
        await streamAsk({ ...selection, question: q, strategies: ['S3'] }, onEvent, controller.signal)
      }
    } catch (err) {
      if (controller.signal.aborted) return
      setError(recording ? `回放未能加载：${err.message}` : err.status ? err.message : '暂时连不上实时服务，请稍后再试；精选题可以直接查看。')
      setRun((r) => r && { ...r, status: 'error', active: null })
    }
  }

  function stop() {
    abortRef.current?.abort()
    setRun((r) => r && { ...r, status: 'error', active: null, error: '已停止。' })
  }

  function chooseCorpus(key) {
    if (key === corpusKey) return
    setCorpusKey(key)
    if (isStatic) setInput('')
  }

  const result = run?.result
  const keyFor = (n) => `${meta?.question_id}:${normNum(n.value)}`

  function add(n) {
    const page = n.pages[0] ?? result.cited_pages[0] ?? null
    const formal = questions.find((q) => q.question_id === meta?.question_id)
    addDraft({
      id: keyFor(n), metric: n.label || formal?.metric || '', value: n.value, unit: n.unit || '', scope: /合并/.test(meta?.question || '') ? '合并' : '',
      corpusKey, company: info?.company, year: info?.report_year, page, status: n.status === 'matched' ? '原文一致' : '计算得出',
      snippet: n.line || (page ? snippetFor(result, n.value, page) : ''), question: meta?.question, time: new Date().toLocaleString('zh-CN'),
    })
  }

  return (
    <div className="lookup-inner">
      {!run && (
        <div className="intro">
          <h1>年报里的每个数，都带着出处</h1>
          <p>{isStatic ? '选一份年报和一道精选问题，查看已录制的查数结果。点开答案里的数字，即可查看年报原页。' : '选一份年报，像问同事一样提问。溯数会检索原文、核对口径，答案里的数字都能点开，直接看到年报原页。'}</p>
        </div>
      )}
      <CorpusSelector corpus={corpus} v3Corpora={v3Corpora} v3Featured={v3Featured} corpusKey={corpusKey} v3Report={v3Report} chooseCorpus={chooseCorpus} />
      <form className="ask" onSubmit={(e) => { e.preventDefault(); ask() }}>
        {v3Report && (
          <div className="data-year" role="group" aria-labelledby="data-year-label">
            <span id="data-year-label">数据年度</span>
            <div className="segmented">
              <button type="button" aria-pressed={!comparative} disabled={!isLive} onClick={() => setComparative(false)}>本年</button>
              <button type="button" aria-pressed={comparative} disabled={!isLive} onClick={() => setComparative(true)}>上年（比较期）</button>
            </div>
          </div>
        )}
        <label htmlFor="q" className="sr-only" style={{ position: 'absolute', left: -9999 }}>问题</label>
        <textarea id="q" rows={2} value={input} disabled={!isLive} aria-describedby="ask-hint"
          placeholder={isStatic ? '自由提问' : v3Report ? `例如：${dataYear} 年合并口径的营业收入是多少？` :
            `问 ${info?.company ?? ''} ${info?.report_year ?? ''} 年报里的一个数，例如：${FREE_EXAMPLES[corpusKey]?.[0] ?? ''}`}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask() } }} />
        <div className="ask-bar">
          <span className="ask-hint" id="ask-hint">{isStatic ? '自由提问仅对专属链接开放，下面是预先运行的真实结果' : '使用论文中表现最好的自适应策略（S3）：每轮核验后判断是否继续，最多检索三轮'}</span>
          {run?.status === 'running' || run?.status === 'waiting'
            ? <button type="button" className="btn ghost" onClick={stop}><Square size={14} />停止</button>
            : isLive && <button type="submit" className="btn" disabled={!info || !input.trim() || status?.error}><ArrowUp size={15} />查数</button>}
        </div>
      </form>

      {mode && (
        <div className="examples">
          <h2>精选问题</h2>
          <div className="featured-questions">
            {v3Report && ownFeatured.length === 0 && <p className="question-meta">这份年报还没有精选回放。</p>}
            {ownFeatured.map((q) => (
              <button className="example" key={q.question_id} type="button" onClick={() => ask(null, q)}>
                <span>{q.question}</span><small>{q.question_id}</small>
                {FEATURED_NOTES[q.question_id] && <small className="featured-note">{FEATURED_NOTES[q.question_id]}</small>}
              </button>
            ))}
          </div>
        </div>
      )}

      {isLive && !v3Report && !run && (
        <div className="examples">
          <h2>从论文的 20 道正式题里挑一道试试，或者直接提问</h2>
          <div className="example-group">
            <h3>常用查数</h3>
            {(FREE_EXAMPLES[corpusKey] || []).map((q) => (
              <button key={q} type="button" className="example" onClick={() => ask(q)}><span>{q}</span></button>
            ))}
          </div>
          {examples.map((g) => (
            <div className="example-group" key={g.type}>
              <h3>{g.type}</h3>
              {g.items.map((q) => (
                <button key={q.question_id} type="button" className="example" onClick={() => ask(q.question)}>
                  <span>{q.question}</span><small>{q.question_id}</small>
                </button>
              ))}
            </div>
          ))}
        </div>
      )}

      {error && <div className="error-box" style={{ marginTop: 16 }}>{error}</div>}

      {run && (
        <section style={{ marginTop: 28 }} aria-live="polite">
          <p className="question-echo">{meta?.question ?? input}</p>
          <div className="question-meta">
            <span>{info?.company} {info?.report_year} 年报</span>
            {meta?.data_year && <span>数据年度 {meta.data_year}</span>}
            {recordedAt && <span>预计算结果 · 录制于 {SHANGHAI_TIME.format(new Date(recordedAt))}</span>}
            <span className="num">{run.status === 'running' ? `已用时 ${elapsed.toFixed(0)} 秒` : result?.elapsed ? `用时 ${result.elapsed} 秒` : ''}</span>
            <PaperVerdict paper={meta?.paper} onOpenMethod={openMethod} />
          </div>
          {run.status === 'running' && run.rounds.length === 0 && !run.active && <p className="question-meta">{recordedAt ? '正在回放…' : status?.ready ? '正在准备检索…' : '检索模型还在加载（启动后约 1 分钟），加载完会自动开始。'}</p>}
          {(run.rounds.length > 0 || run.active) && <RoundTrace run={run} />}
          {run.status === 'error' && <div className="error-box">{run.error}</div>}
          {result && <AnswerSheet result={result} corpusKey={corpusKey} openPdf={openPdf} add={add} added={(n) => draftIds.has(keyFor(n))} />}
        </section>
      )}
    </div>
  )
}

function AnswerSheet({ result, corpusKey, openPdf, add, added }) {
  const onPage = (page) => openPdf({ corpusKey, page, values: result.numbers.filter((n) => n.pages.includes(page)).map((n) => n.value) })
  const onFigure = (n) => openPdf({ corpusKey, page: n.pages[0], values: [n.value] })
  const boundary = result.boundary_year
  const refused = result.refused
  const figures = result.numbers || []
  return (
    <article className="sheet">
      <div className="sheet-head">
        <h2>{refused ? '没有找到可核验的答案' : '答案'}</h2>
        <span className="sheet-stats">{result.rounds} 轮 · {result.model_calls} 次模型调用 · {(result.total_tokens || 0).toLocaleString()} tokens · {result.stop_reason}</span>
      </div>
      {result.answer_check?.some((f) => f.severity === 'block') && (
        <p className="check-notice" role="note">
          本回答复述了检索系统的标注（{[...new Set(result.answer_check.filter((f) => f.severity === 'block').map((f) => f.detail))].join('、')}），这不是年报原文。请以右侧原页为准。
        </p>
      )}
      {!boundary && result.answer && <AnswerText text={result.answer} foldRevision numbers={figures} onPage={onPage} onFigure={onFigure} />}
      {refused && (
        <div className="refusal">
          <h3>{boundary ? `超出年报覆盖范围` : '溯数没有用相近口径替代，也没有编数'}</h3>
          {boundary
            ? <p style={{ margin: 0 }}>问题问的是 {boundary} 年，这份是 {result.corpus_year} 年年报，不包含 {boundary} 年的数据。溯数没有调用模型，避免它凭空作答。</p>
            : <>
              <div>还缺：</div>
              <ul>{(result.missing || []).map((m, i) => <li key={i}>{m}</li>)}</ul>
              {result.suggested_pages?.length > 0 && <p style={{ margin: '8px 0 0' }}>最可能相关的页面：{result.suggested_pages.map((p) => <button key={p} className="page-link" type="button" onClick={() => onPage(p)}>第{p}页</button>)}，可以点开自己核对。</p>}
              <p style={{ margin: '6px 0 0' }}>换个说法再问也许能找到，比如写出报表名称（“合并利润表”）或附注里的科目名。</p>
            </>}
        </div>
      )}
      {!refused && figures.length > 0 && (
        <div className="verify-list">
          <h3>数字核验（{figures.filter((n) => n.status === 'matched').length} 个原文一致，{figures.filter((n) => n.status !== 'matched').length} 个计算得出）</h3>
          {figures.map((n) => (
            <div className="verify-row" key={n.value}>
              <span>{n.status === 'matched' ? <TickMark className="tickmark mark-ok" /> : <span title="计算得出">∑</span>}</span>
              <span>
                <span className="v">{n.value}</span>{' '}
                {n.label && <small style={{ marginRight: 6 }}>{n.label}{n.unit ? `（${n.unit}）` : ''}</small>}
                {n.status === 'matched'
                  ? <small>原文 {n.pages.map((p) => <button key={p} type="button" className="page-link" onClick={() => openPdf({ corpusKey, page: p, values: [n.value] })}>第{p}页</button>)}</small>
                  : <small>计算得出，请核对计算过程</small>}
              </span>
              <span />
              {added(n)
                ? <span className="added"><Check size={13} /> 已加入</span>
                : <button type="button" className="btn ghost small" onClick={() => add(n)}><Plus size={13} />加入底稿</button>}
            </div>
          ))}
        </div>
      )}
    </article>
  )
}
