import { useEffect, useMemo, useRef, useState } from 'react'
import { Play, Square, History, Star } from 'lucide-react'
import { streamAsk, streamReplay, featureRecording, getRecordings, emptyRun, reduceRun, STRATEGY_INFO } from '../api.js'
import { RoundTrace, useElapsed } from './Lookup.jsx'
import AnswerText from './AnswerText.jsx'

const STRATEGIES = ['S1', 'S2', 'S3']
const SPEEDS = [1, 4, 8]

function formatTime(iso) {
  const d = new Date(iso)
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日 ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

export default function Lab({ mode, featured, corpus, questions, openPdf, budget }) {
  const options = useMemo(() => featured.map(({ question_id }) => questions.find((q) => q.question_id === question_id)).filter(Boolean), [featured, questions])
  const isLive = mode === 'live'
  const [picked, setPicked] = useState(null)
  const [runs, setRuns] = useState(null)
  const [meta, setMeta] = useState(null)
  const [error, setError] = useState(null)
  const [tapes, setTapes] = useState([])
  const [tapeId, setTapeId] = useState(null)
  const [speed, setSpeed] = useState(4)
  const [runKind, setRunKind] = useState(null)
  const abortRef = useRef(null)
  const startedAt = runs?.S1?.startedAt
  const running = runs && STRATEGIES.some((s) => runs[s].status === 'running' || runs[s].status === 'waiting')
  const elapsed = useElapsed(running, startedAt)

  useEffect(() => {
    if (!picked) return
    let active = true
    getRecordings(picked.question_id).then((list) => {
      if (!active) return
      const full = list.filter((t) => STRATEGIES.every((s) => t.strategies.includes(s)))
      setTapes(full)
      setTapeId((full.find((t) => t.featured) || full[0])?.id ?? null)
    }).catch((err) => { if (active) setError(`运行记录未能加载：${err.message}`) })
    return () => { active = false; abortRef.current?.abort() }
  }, [picked])

  function pick(question) {
    if (question === picked) return
    abortRef.current?.abort()
    setPicked(question)
    setRuns(null)
    setMeta(null)
    setRunKind(null)
    setError(null)
    setTapes([])
    setTapeId(null)
  }

  function markStopped(message) {
    setRuns((previous) => previous && Object.fromEntries(STRATEGIES.map((s) => {
      const run = previous[s]
      return [s, run.status === 'done' || run.status === 'error' ? run : { ...run, status: 'error', active: null, error: message }]
    })))
  }

  async function start(kind) {
    if (!picked || (kind === 'live' ? !isLive : !tapeId)) return
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    let current = Object.fromEntries(STRATEGIES.map((s) => [s, emptyRun(s)]))
    setRuns(current); setMeta(null); setError(null); setRunKind(kind)
    const onEvent = (event) => {
      if (controller.signal.aborted) return
      if (event.type === 'meta') setMeta(event)
      else if (event.strategy) {
        current = { ...current, [event.strategy]: reduceRun(current[event.strategy], event) }
        setRuns(current)
      }
    }
    try {
      if (kind === 'replay') await streamReplay(tapeId, speed, onEvent, controller.signal)
      else await streamAsk({ company: picked.company, question: picked.question, strategies: STRATEGIES }, onEvent, controller.signal)
    } catch (err) {
      if (controller.signal.aborted) return
      markStopped(null)
      setError(kind === 'replay' ? `回放未能加载：${err.message}` : `没有连上后端：${err.message}`)
    }
  }

  async function makeDefault(id) {
    try {
      await featureRecording(id)
      setTapes((previous) => previous.map((recording) => ({ ...recording, featured: recording.id === id })))
    } catch (err) { setError(err.message) }
  }

  const paper = meta?.paper?.results || picked?.results || {}
  const info = picked && corpus.find((c) => c.code === picked.company)
  const tape = tapes.find((t) => t.id === tapeId)
  const replayInfo = runKind === 'replay' ? meta?.replay : null

  return (
    <div className="page">
      <h1 className="page-title">三策略实验台</h1>
      <p className="page-lead">
        论文比较了三种检索策略：单轮、固定两轮、自适应最多三轮。{isLive ? '这里用同一道题同时运行三者' : '这里可回放同一道题的三种策略'}，每一栏都能看到逐轮检索、规则核验、停止原因和预算消耗，并与论文中那次运行的盲评结果对照。
      </p>

      <div className="lab-picker" role="group" aria-label="选择题目" style={{ margin: 0 }}>
        {options.map((q) => (
          <button key={q.question_id} type="button" className="chip" aria-pressed={picked?.question_id === q.question_id} onClick={() => pick(q)}>
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
            {running && <span className="num">{runKind === 'replay' ? '回放中' : '已用时'} {elapsed.toFixed(0)} 秒</span>}
          </div>

          <div className="lab-controls">
            {running
              ? <button className="btn ghost" type="button" onClick={() => { abortRef.current?.abort(); markStopped('已停止。') }}><Square size={14} />停止</button>
              : <>
                {tape && <button className="btn" type="button" onClick={() => start('replay')}><History size={14} />回放运行记录</button>}
                {isLive && <button className={tape ? 'btn ghost' : 'btn'} type="button" onClick={() => start('live')}><Play size={14} />实时运行三种策略</button>}
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
                      : import.meta.env.DEV && isLive && <button type="button" className="btn ghost small" onClick={() => makeDefault(t.id)}>设为演示默认</button>}
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
          <span>{running ? '正在回放' : '回放记录'} <b>{formatTime(replayInfo.recorded_at)}</b> 的一次实时运行，{replayInfo.speed} 倍速；原始用时 {Math.round(replayInfo.duration)} 秒。事件顺序和结果与当时完全一致。</span>
        </div>
      )}

      <div className="lab-grid" style={{ marginTop: 18 }}>
        {STRATEGIES.map((s) => (
          <Lane key={s} strategy={s} run={runs?.[s]} paper={paper[s]} budget={meta?.budget ?? budget} replay={runKind === 'replay'}
            onPage={(page, values = []) => openPdf({ company: picked.company, page, values })} />
        ))}
      </div>
      <p className="lab-note">
        {isLive ? '实时运行结束后会保存符合条件的记录。大模型的输出有随机性，同一道题再跑一次，轮次和答案都可能不同。' : '每道精选题提供一次真实运行的录制。'}论文结论基于 20 道题、每格一次运行的盲评，属于描述性比较。预算上限与论文一致：每题每组 39,000 tokens。
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
          <AnswerText text={result.answer || '（无答案）'} numbers={result.numbers}
            onPage={(page) => onPage(page, (result.numbers || []).filter((n) => n.pages.includes(page)).map((n) => n.value))}
            onFigure={(n) => onPage(n.pages[0], [n.value])} />
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
