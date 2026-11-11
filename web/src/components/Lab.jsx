import { useEffect, useMemo, useRef, useState } from 'react'
import { Play, Square, History, Star } from 'lucide-react'
import { streamAsk, streamReplay, featureRecording, getJSON, emptyRun, reduceRun, STRATEGY_INFO } from '../api.js'
import { RoundTrace, useElapsed } from './Lookup.jsx'
import AnswerText from './AnswerText.jsx'

const STRATEGIES = ['S1', 'S2', 'S3']
// Ordered by how reliably live runs reproduce the paper (prewarm rehearsals, 2026-11-11):
// WSI-2025-005  S1 ✗ in 3/3 runs, S3 ✓ in 2/3 (corrects itself in round 3)
// BYD-2025-002  S1 ✗ / S2 ✓ / S3 ✓, same as the paper
// WSI-2025-008  S3 ✓ (22%, cross-page), S1 ✗
// WSI-2025-007  all ✓; S3 stops after round 1 with a third of S2's tokens
// BYD-2025-010  boundary question (2026 data is not in the 2025 report)
const FEATURED = ['WSI-2025-005', 'BYD-2025-002', 'WSI-2025-008', 'WSI-2025-007', 'BYD-2025-010']
const SPEEDS = [1, 4, 8]

function formatTime(iso) {
  const d = new Date(iso)
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日 ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

export default function Lab({ corpus, questions, openPdf, budget }) {
  const featured = useMemo(() => FEATURED.map((id) => questions.find((q) => q.question_id === id)).filter(Boolean), [questions])
  const [picked, setPicked] = useState(null)
  const [runs, setRuns] = useState(null)
  const [meta, setMeta] = useState(null)
  const [error, setError] = useState(null)
  const [tapes, setTapes] = useState([])
  const [tapeId, setTapeId] = useState(null)
  const [speed, setSpeed] = useState(4)
  const [mode, setMode] = useState(null) // 'live' | 'replay'
  const abortRef = useRef(null)
  const startedAt = runs?.S1?.startedAt
  const running = runs && STRATEGIES.some((s) => runs[s].status === 'running' || runs[s].status === 'waiting')
  const elapsed = useElapsed(running, startedAt)

  async function loadTapes(qid, keep) {
    try {
      const list = await getJSON(`/api/recordings?question_id=${encodeURIComponent(qid)}`)
      const full = list.filter((t) => STRATEGIES.every((s) => t.strategies.includes(s)))
      setTapes(full)
      if (!keep) setTapeId((full.find((t) => t.featured) || full[0])?.id ?? null)
    } catch { setTapes([]) }
  }

  useEffect(() => {
    if (!picked) return
    abortRef.current?.abort()
    setRuns(null); setMeta(null); setMode(null); setError(null)
    loadTapes(picked.question_id)
  }, [picked])

  async function start(kind) {
    if (!picked) return
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    let current = Object.fromEntries(STRATEGIES.map((s) => [s, emptyRun(s)]))
    setRuns(current); setMeta(null); setError(null); setMode(kind)
    const onEvent = (event) => {
      if (event.type === 'meta') setMeta(event)
      else if (event.strategy) {
        current = { ...current, [event.strategy]: reduceRun(current[event.strategy], event) }
        setRuns(current)
      }
    }
    try {
      if (kind === 'replay') await streamReplay(tapeId, speed, onEvent, controller.signal)
      else {
        await streamAsk({ company: picked.company, question: picked.question, strategies: STRATEGIES }, onEvent, controller.signal)
        loadTapes(picked.question_id, true)
      }
    } catch (err) {
      if (err.name !== 'AbortError') setError(`没有连上后端：${err.message}`)
    }
  }

  async function makeDefault(id) {
    try { await featureRecording(id); loadTapes(picked.question_id, true) } catch (err) { setError(err.message) }
  }

  const paper = meta?.paper?.results || picked?.results || {}
  const info = picked && corpus.find((c) => c.code === picked.company)
  const tape = tapes.find((t) => t.id === tapeId)
  const replayInfo = mode === 'replay' ? meta?.replay : null

  return (
    <div className="page">
      <h1 className="page-title">三策略实验台</h1>
      <p className="page-lead">
        论文比较了三种检索策略：单轮、固定两轮、自适应最多三轮。这里用同一道题同时运行三者，每一栏都能看到逐轮检索、规则核验、停止原因和预算消耗，并与论文中那次运行的盲评结果对照。
      </p>

      <div className="lab-picker" role="group" aria-label="选择题目" style={{ margin: 0 }}>
        {featured.map((q) => (
          <button key={q.question_id} type="button" className="chip" aria-pressed={picked?.question_id === q.question_id} onClick={() => setPicked(q)}>
            {q.question_id} · {q.question_type}
          </button>
        ))}
      </div>

      {picked && (
        <>
          <p className="question-echo" style={{ marginTop: 14 }}>{picked.question}</p>
          <div className="question-meta">
            <span>{info?.company} {info?.year} 年报</span>
            <span>论文盲评：{STRATEGIES.map((s) => paper[s] && <span key={s} style={{ marginRight: 8 }}>{s} <span className={paper[s].correct ? 'mark-ok' : 'mark-bad'}>{paper[s].correct ? '✓ 正确' : '✗ 错误'}</span></span>)}</span>
            {running && <span className="num">{mode === 'replay' ? '回放中' : '已用时'} {elapsed.toFixed(0)} 秒</span>}
          </div>

          <div className="lab-controls">
            {running
              ? <button className="btn ghost" type="button" onClick={() => abortRef.current?.abort()}><Square size={14} />停止</button>
              : <>
                {tape && <button className="btn" type="button" onClick={() => start('replay')}><History size={14} />回放运行记录</button>}
                <button className={tape ? 'btn ghost' : 'btn'} type="button" onClick={() => start('live')}><Play size={14} />实时运行三种策略</button>
                {tape && (
                  <label className="speed">回放速度
                    <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
                      {SPEEDS.map((s) => <option key={s} value={s}>{s} 倍</option>)}
                    </select>
                  </label>
                )}
              </>}
          </div>

          {tapes.length > 0 && !running && (
            <details className="tapes">
              <summary>运行记录（{tapes.length} 次实时运行）{tape ? `：已选 ${formatTime(tape.recorded_at)}` : ''}</summary>
              <ul>
                {tapes.map((t) => (
                  <li key={t.id}>
                    <label>
                      <input type="radio" name="tape" checked={t.id === tapeId} onChange={() => setTapeId(t.id)} />
                      <span className="num">{formatTime(t.recorded_at)}</span>
                      {STRATEGIES.map((s) => {
                        const r = t.summary[s]
                        return r && <span key={s} className={`flag ${r.refused ? 'bad' : 'ok'}`}>{s} {r.refused ? '拒答' : '作答'} {r.rounds} 轮</span>
                      })}
                      <span className="num tape-time">原始用时 {Math.round(t.duration)} 秒</span>
                    </label>
                    {t.featured
                      ? <span className="tape-default"><Star size={12} /> 演示默认</span>
                      : <button type="button" className="btn ghost small" onClick={() => makeDefault(t.id)}>设为演示默认</button>}
                    <p className="tape-answer">S3：{t.summary.S3?.answer}</p>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
      {error && <div className="error-box" style={{ margin: '12px 0' }}>{error}</div>}

      {replayInfo && (
        <div className="replay-banner" role="status">
          <History size={16} />
          <span>正在回放 <b>{formatTime(replayInfo.recorded_at)}</b> 的一次实时运行记录，{replayInfo.speed} 倍速；原始用时 {Math.round(replayInfo.duration)} 秒。事件顺序和结果与当时完全一致。</span>
        </div>
      )}

      <div className="lab-grid" style={{ marginTop: 18 }}>
        {STRATEGIES.map((s) => (
          <Lane key={s} strategy={s} run={runs?.[s]} paper={paper[s]} budget={meta?.budget ?? budget} replay={mode === 'replay'}
            onPage={(page) => openPdf({ company: picked.company, page, values: [] })} />
        ))}
      </div>
      <p className="lab-note">
        每次实时运行都会自动保存为运行记录，可以原样回放。大模型的输出有随机性，同一道题再跑一次，轮次和答案都可能不同；论文结论基于 20 道题、每格一次运行的盲评，属于描述性比较。预算上限与论文一致：每题每组 39,000 tokens。
      </p>
    </div>
  )
}

function Lane({ strategy, run, paper, budget, onPage, replay }) {
  const meta = STRATEGY_INFO[strategy]
  const tokens = run?.tokens || 0
  const pct = Math.min(100, (tokens / (budget || 39000)) * 100)
  const result = run?.result
  return (
    <section className="lane" aria-label={`${strategy} ${meta.name}`}>
      <header className="lane-head">
        <h3>{strategy} {meta.name}<span>{meta.rule}</span></h3>
        <p>{run?.status === 'running' ? (replay ? '回放中' : '运行中') : run?.status === 'done' ? '已完成' : run?.status === 'error' ? '出错' : '等待运行'}</p>
        <div className="budget" role="meter" aria-valuemin={0} aria-valuemax={budget} aria-valuenow={tokens} aria-label="预算消耗"><i style={{ width: `${pct}%` }} /></div>
        <div className="budget-legend"><span>{tokens.toLocaleString()} tokens</span><span>上限 {(budget || 39000).toLocaleString()}</span></div>
      </header>
      <div className="lane-body">
        {run && (run.rounds.length > 0 || run.active) ? <RoundTrace run={run} compact /> : <p className="question-meta">{run ? '正在检索…' : '选一道题后开始运行。'}</p>}
        {run?.error && <div className="error-box">{run.error}</div>}
        {result && <div className="lane-stop">停止原因：<b>{result.stop_reason}</b></div>}
      </div>
      {result && (
        <div className="lane-answer">
          <AnswerText text={result.answer || '（无答案）'} numbers={result.numbers} onPage={onPage} onFigure={(n) => onPage(n.pages[0])} />
        </div>
      )}
      <footer className="lane-foot">
        <div className="row"><span>{replay ? '这次记录' : '本次'}</span><span className="num">{result ? `${result.rounds} 轮 · ${result.model_calls} 次调用 · ${(result.total_tokens || 0).toLocaleString()} tokens · ${result.elapsed}s` : '—'}</span></div>
        <div className="row"><span>论文那次</span><span className="num">{paper ? <>{paper.rounds} 轮 · {paper.tokens.toLocaleString()} tokens · 盲评 <span className={paper.correct ? 'mark-ok' : 'mark-bad'}>{paper.correct ? '✓' : '✗'}</span></> : '—'}</span></div>
        {paper?.reason && <div style={{ color: 'var(--ink-3)', fontSize: 12 }}>评语：{paper.reason}</div>}
      </footer>
    </section>
  )
}
