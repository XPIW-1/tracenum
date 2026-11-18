export async function getJSON(path, signal) {
  const res = await fetch(path, { signal })
  if (!res.ok) throw new Error(`${path} ${res.status}`)
  return res.json()
}

// Private link: https://tracenum.<domain>/#k=<code>. The fragment never reaches the
// server or a Referer; we post it once for an HttpOnly cookie and drop it from the URL.
async function consumeAccessFragment() {
  const match = location.hash.match(/[#&]k=([\w-]{8,64})/)
  if (!match) return
  history.replaceState(null, '', `${location.pathname}${location.search}#lookup`)
  try {
    await fetch('/api/access/session', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: match[1] }), credentials: 'same-origin',
    })
  } catch { /* backend off: stay in the public showcase */ }
}

// 'live' only when the backend says this visitor may ask live questions right now.
export async function detectMode() {
  await consumeAccessFragment()
  try {
    const session = await getJSON('/api/session', AbortSignal.timeout(4000))
    if (session?.live === true) return 'live'
  } catch { /* A static host returns HTML or 404 for /api/session. */ }
  return 'static'
}

export async function getRecordings(questionId) {
  const recordings = await getJSON('/data/recordings/index.json')
  return questionId ? recordings.filter((recording) => recording.question_id === questionId) : recordings
}

// POST + server-sent events over fetch (EventSource cannot POST).
export function streamAsk(body, onEvent, signal) {
  return readEvents(fetch('/api/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  }), onEvent)
}

// Replays a recorded live run with its original event order and (scaled) timing.
export function streamReplay(id, speed, onEvent, signal) {
  return playRecording(id, speed, onEvent, signal)
}

export function replayStrategy(id, strategy, speed, onEvent, signal) {
  return playRecording(id, speed, onEvent, signal, strategy)
}

async function playRecording(id, speed, onEvent, signal, strategy) {
  const record = await getJSON(`/data/recordings/${encodeURIComponent(id)}.json`, signal)
  const rate = Number(speed)
  speed = Number.isNaN(rate) ? 4 : Math.min(20, Math.max(1, rate))
  const replay = { id, recorded_at: record.recorded_at, speed, duration: record.duration }
  let last = 0
  for (const { t, event } of record.events) {
    signal?.throwIfAborted()
    if (strategy && event.strategy !== strategy && event.type !== 'meta' && event.type !== 'done') continue
    const gap = (t - last) / speed
    last = t
    if (gap > 0) await replayDelay(Math.min(gap, 4) * 1000, signal)
    signal?.throwIfAborted()
    if (event.type === 'meta') {
      onEvent({ ...event, strategies: strategy ? [strategy] : event.strategies, replay })
    } else {
      onEvent(event)
    }
  }
}

function replayDelay(ms, signal) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', abort)
      resolve()
    }, ms)
    function abort() {
      clearTimeout(timer)
      reject(signal.reason)
    }
    signal?.addEventListener('abort', abort, { once: true })
  })
}

export const featureRecording = import.meta.env.DEV ? async (id) => {
  const res = await fetch(`/api/recordings/${encodeURIComponent(id)}/feature`, { method: 'POST' })
  if (!res.ok) throw new Error(`设置失败（${res.status}）`)
} : undefined

async function readEvents(request, onEvent) {
  const res = await request
  if (!res.ok || !res.body) {
    // Refusals (no access, daily limit, busy, budget) carry a readable Chinese message.
    let message = `后端返回 ${res.status}`
    try { message = (await res.json()).message || message } catch { /* not JSON */ }
    const error = new Error(message)
    error.status = res.status
    throw error
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let cut
    while ((cut = buffer.indexOf('\n\n')) >= 0) {
      const chunk = buffer.slice(0, cut)
      buffer = buffer.slice(cut + 2)
      const line = chunk.split('\n').find((l) => l.startsWith('data:'))
      if (line) onEvent(JSON.parse(line.slice(5)))
    }
  }
}

const DRAFT_COLUMNS = [
  ['指标', 'metric', 22], ['数值', 'value', 18], ['单位', 'unit', 8], ['口径', 'scope', 12],
  ['公司', 'company', 10], ['年度', 'year', 8], ['页码', 'page', 8], ['核验状态', 'status', 14],
  ['原文片段', 'snippet', 60], ['提问', 'question', 48], ['查询时间', 'time', 20],
]
const DRAFT_NOTE = '本底稿由溯数 TraceNum 导出。页码为年报 PDF 物理页；核验状态“原文一致”表示该数值在所引页的原文中逐字出现，“计算得出”表示由原文数值计算而来，需复核计算过程。'

function asNumber(text) {
  const raw = String(text).replace(/[,，]/g, '').trim()
  const negative = raw.startsWith('-') || (raw.startsWith('(') && raw.endsWith(')'))
  const numberText = raw.replace(/^[-()（）%]+|[-()（）%]+$/g, '').trim()
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(numberText)) return text
  const value = Number(numberText)
  return Number.isFinite(value) ? (negative ? -value : value) : text
}

export async function exportDraft(items) {
  const { default: writeExcelFile } = await import('write-excel-file/browser')
  const header = DRAFT_COLUMNS.map(([value]) => ({ value, fontWeight: 'bold', textColor: '#FFFFFF', backgroundColor: '#172033' }))
  const rows = items.map((item) => DRAFT_COLUMNS.map(([, key]) => {
    const raw = item[key] ?? ''
    const value = key === 'value' ? asNumber(raw) : raw
    const cell = { value, wrap: true, alignVertical: 'top' }
    if (key === 'value' && typeof value === 'number') cell.format = String(raw).includes('.') ? '#,##0.00' : '#,##0'
    return cell
  }))
  await writeExcelFile([
    { sheet: '底稿', data: [header, ...rows], columns: DRAFT_COLUMNS.map(([, , width]) => ({ width })), stickyRowsCount: 1 },
    { sheet: '说明', data: [[DRAFT_NOTE]] },
  ]).toFile(`溯数底稿_${new Date().toISOString().slice(0, 10)}.xlsx`)
}

// Reduce a stream of events into per-strategy run state.
export function emptyRun(strategy) {
  return { strategy, status: 'waiting', rounds: [], active: null, tokens: 0, result: null, error: null, startedAt: null }
}

export function reduceRun(run, event) {
  const next = { ...run }
  if (event.type === 'start') {
    next.status = 'running'
    next.startedAt = Date.now()
  } else if (event.type === 'node_start') {
    // requery runs at the end of round n and opens round n + 1.
    next.active = { node: event.node, round: event.node === 'requery' ? event.round + 1 : event.round }
  } else if (event.type === 'progress') {
    if (next.active) next.active = { ...next.active, done: event.done, total: event.total }
  } else if (event.type === 'node') {
    const rounds = [...next.rounds]
    let row = rounds.find((r) => r.round === event.round)
    if (!row) {
      row = { round: event.round }
      rounds.push(row)
    } else {
      rounds[rounds.indexOf(row)] = row = { ...row }
    }
    row[event.node] = event
    next.rounds = rounds
    next.tokens = event.total_tokens ?? next.tokens
    next.budget = event.budget ?? next.budget
    next.active = null
  } else if (event.type === 'final') {
    next.status = 'done'
    next.result = event.result
    next.tokens = event.result.total_tokens ?? next.tokens
    next.active = null
  } else if (event.type === 'error') {
    next.status = 'error'
    next.error = event.message
    next.active = null
  }
  return next
}

export const NODE_LABEL = {
  retrieve: '检索与重排',
  generate: '生成答案',
  check: '规则核验',
  decide: '停止判断',
  requery: '改写检索词',
}

export const STRATEGY_INFO = {
  S1: { name: '单轮', rule: '检索一次、回答一次' },
  S2: { name: '固定两轮', rule: '无论核验结果，都检索两轮' },
  S3: { name: '自适应', rule: '按停止规则判断，最多三轮' },
}
