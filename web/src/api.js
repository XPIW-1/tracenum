export async function getJSON(path) {
  const res = await fetch(path)
  if (!res.ok) throw new Error(`${path} ${res.status}`)
  return res.json()
}

// POST + server-sent events over fetch (EventSource cannot POST).
export async function streamAsk(body, onEvent, signal) {
  const res = await fetch('/api/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok || !res.body) throw new Error(`后端返回 ${res.status}`)
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

export async function exportDraft(items) {
  const res = await fetch('/api/export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items }),
  })
  if (!res.ok) throw new Error(`导出失败（${res.status}）`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `溯数底稿_${new Date().toISOString().slice(0, 10)}.xlsx`
  a.click()
  URL.revokeObjectURL(url)
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
