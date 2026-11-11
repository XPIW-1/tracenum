import { useEffect } from 'react'
import { Download, Copy, Trash2, X } from 'lucide-react'
import { exportDraft } from '../api.js'

export default function Draft({ items, setItems, onClose, openPdf, corpus, toast }) {
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const update = (id, patch) => setItems(items.map((it) => (it.id === id ? { ...it, ...patch } : it)))
  const codeOf = (company) => corpus.find((c) => c.company === company)?.code

  async function copyMarkdown() {
    const head = '| 指标 | 数值 | 单位 | 口径 | 公司 | 年度 | 页码 | 核验状态 |\n|---|---:|---|---|---|---|---:|---|'
    const rows = items.map((i) => `| ${i.metric} | ${i.value} | ${i.unit} | ${i.scope} | ${i.company} | ${i.year} | ${i.page ?? ''} | ${i.status} |`)
    await navigator.clipboard.writeText([head, ...rows].join('\n'))
    toast('已复制为 Markdown 表格')
  }

  async function download() {
    try {
      await exportDraft(items)
      toast('已导出 Excel 底稿')
    } catch (err) {
      toast(err.message)
    }
  }

  return (
    <>
      <div className="drawer-scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label="底稿">
        <div className="drawer-head">
          <h2>底稿</h2>
          <span className="question-meta" style={{ margin: 0 }}>{items.length} 条</span>
          <span style={{ flex: 1 }} />
          <button className="icon-btn" aria-label="关闭底稿" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="drawer-body">
          {items.length === 0 && <p className="drawer-empty">还没有数据。在答案下方的数字核验里点“加入底稿”，查到的数会带着页码和核验状态汇总到这里。</p>}
          {items.map((it) => (
            <div className="draft-item" key={it.id}>
              <input aria-label="指标名称" value={it.metric} placeholder="填写指标名称，例如：投资活动现金流量净额" onChange={(e) => update(it.id, { metric: e.target.value })} />
              <div className="draft-line">
                <span className="v">{it.value}</span>
                <input aria-label="单位" style={{ width: 70 }} value={it.unit} placeholder="单位" onChange={(e) => update(it.id, { unit: e.target.value })} />
                <input aria-label="口径" style={{ width: 80 }} value={it.scope} placeholder="口径" onChange={(e) => update(it.id, { scope: e.target.value })} />
                <span>{it.company} {it.year}</span>
                {it.page && <button type="button" className="page-link" onClick={() => openPdf({ company: codeOf(it.company), page: it.page, values: [it.value] })}>第{it.page}页</button>}
                <span className={it.status === '原文一致' ? 'mark-ok' : ''}>{it.status}</span>
                <span style={{ flex: 1 }} />
                <button type="button" className="icon-btn" aria-label="删除这一条" onClick={() => setItems(items.filter((x) => x.id !== it.id))}><Trash2 size={14} /></button>
              </div>
              {it.snippet && <small style={{ color: 'var(--ink-3)' }}>原文：…{it.snippet}…</small>}
            </div>
          ))}
        </div>
        <div className="drawer-foot">
          <button className="btn" disabled={!items.length} onClick={download}><Download size={14} />导出 Excel</button>
          <button className="btn ghost" disabled={!items.length} onClick={copyMarkdown}><Copy size={14} />复制为 Markdown</button>
        </div>
      </aside>
    </>
  )
}
