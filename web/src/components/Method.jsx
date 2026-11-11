import { useMemo, useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'

const GOLD_EXAMPLE = `题号  BYD-2025-003（比亚迪，基础财务事实）
问题  2025年合并口径下，投资活动产生的现金流量净额相较2024年变动了多少（千元）？原因是什么？
标准答案  -68,380,827 千元；主要是购建固定资产、无形资产和其他长期资产支付的现金增加
必需事实  F14 2025年净额 -197,463,109,000.00 元
          F15 2024年净额 -129,082,282,000.00 元
          F16 同比变动 52.97%，原因说明
可接受证据  第 40 页“5.现金流”；第 130 页合并现金流量表
计算  (2025年净额 − 2024年净额) / 1000
容差  保留小数均可；或写“现金净流出增加 68,380,827 千元”
编写 B · 复核 A`

const STEPS = [
  { title: '准备语料', sub: '两份 2025 年年报，PDF 文本与表格提取、切块、建索引',
    body: <>比亚迪年报 268 页切成 1,828 个片段，威胜信息年报 262 页切成 1,038 个片段。表格保留表名、口径、单位和年份，跨页续表继承上一页的表头。每份 PDF、切块文件和索引都记录了 SHA-256，之后任何改动都会被发现。</> },
  { title: '编写 20 道正式题', sub: '五类题型，每家公司 10 道',
    body: <>基础财务事实、相似指标与财务口径、多证据计算、跨页证据、语料边界。题型针对的是年报问答最容易出错的地方：口径混用、跨页取数和超出年报范围的问题。</> },
  { title: '建立 Gold 标准答案', sub: '必需事实、可接受证据、计算公式与容差',
    body: <><p style={{ margin: '0 0 8px' }}>每道题的标准答案拆成可以逐项核对的部分，由一人编写、另一人复核。下面是一道题的完整 Gold：</p><pre>{GOLD_EXAMPLE}</pre></> },
  { title: '设计三组策略', sub: 'S1 单轮、S2 固定两轮、S3 自适应最多三轮',
    body: <>三组共用同一套检索、重排、生成和规则化反馈，只在“什么时候停”上不同。S2 不看核验结果，固定检索两轮；S3 按停止规则判断，证据、计算、年度、口径四项反馈全部通过、无新增证据或超出核验范围时停止，否则改写检索词继续，最多三轮。</> },
  { title: '设定预算与停止规则', sub: '每题每组 39,000 tokens，调用前检查',
    body: <>每次调用模型之前先估算输入长度，确认剩余预算够用再调用，不够就停止并记录原因。每轮结束后按固定优先级判断是否停止：预算停止或耗尽 → 语料边界外 → 无新增证据（第 2 轮起）→ 核验范围外 → 证据齐全（四项反馈全部为真）→ 达到轮次上限。</> },
  { title: '冻结实验', sub: '题集、Gold、语料、代码、配置全部哈希锁定',
    body: <>2026 年 10 月 26 日冻结。运行脚本在执行前逐项校验 13 个源码文件、题集、Gold、语料和配置的哈希，任何一项对不上就拒绝运行。每个题目与策略的组合只运行一次，不重试、不挑选。</> },
  { title: '运行 60 次', sub: '20 题 × 3 策略，记录每一轮的检索、上下文和调用',
    body: <>每次运行都保存逐轮检索候选、进入上下文的证据、模型调用的 token 数和停止原因。60 次运行的本地估算费用约 2 元。</> },
  { title: '匿名盲评', sub: '评分者看不到策略名，按 13 类错误编码打分',
    body: <>评价答案准确性、证据完整性、引用支持和计算正确性。错误编码包括：E01 数值错误、E04 财务口径误用、E07 引用不支持、E10 错误拒答等。评分锁定之后才揭盲。</> },
  { title: '揭盲', sub: '还原每条记录对应的策略，统计结果', body: <>结果见下方。</> },
]

const DESIGN = [
  ['失败主要是证据没进上下文，而不是算错', '答案里每个数都标出出处页，点开就是年报原页；找不到时列出最可能相关的页面'],
  ['S3 比 S2 多答对的 4 道题，都是在第 3 轮才答对', '默认使用 S3：核验没通过就改写检索词继续，最多三轮'],
  ['自适应停止在易答题上省下约一半消耗', '易答题往往第 1 轮就停，不为“保险”多跑'],
  ['模型找不到扣非口径时选择拒答，而不是拿相近口径替代', '拒答时说明缺什么，绝不用相近口径凑数'],
  ['带页码的引用不等于引用真的支持结论', '逐个数字核对：原文逐字出现的打勾，计算得出的单独标出'],
]

export default function Method({ questions, goLab }) {
  const [open, setOpen] = useState(2)
  const stats = useMemo(() => {
    const out = {}
    for (const s of ['S1', 'S2', 'S3']) {
      const rows = questions.map((q) => q.results?.[s]).filter(Boolean)
      out[s] = { n: rows.length, correct: rows.filter((r) => r.correct).length, tokens: rows.reduce((a, r) => a + r.tokens, 0) / (rows.length || 1) }
    }
    return out
  }, [questions])
  const ratio = stats.S1?.tokens ? stats.S3.tokens / stats.S1.tokens : 0

  return (
    <div className="page">
      <h1 className="page-title">实验设计</h1>
      <p className="page-lead">
        溯数的设计来自课程论文《资源预算约束下的金融年报问答：自适应多轮检索与外部反馈修正》。我们先用一个冻结、盲评的实验弄清楚检索策略在年报问答里怎样失败、怎样改进，再把结论做进产品。
      </p>

      <ol className="steps">
        {STEPS.map((s, i) => (
          <li className="step" key={s.title}>
            <button type="button" aria-expanded={open === i} onClick={() => setOpen(open === i ? -1 : i)}>
              <span className="step-n">{i + 1}</span>
              <span className="step-title">{s.title}<span className="step-sub">{s.sub}</span></span>
              {open === i ? <ChevronDown size={18} /> : <ChevronRight size={18} />}
            </button>
            {open === i && <div className="step-body">{s.body}</div>}
          </li>
        ))}
      </ol>

      <section className="results">
        <h2>结果：多检索一轮，多答对一批题</h2>
        <div className="bars">
          {['S1', 'S2', 'S3'].map((s) => (
            <div className="bar-row" key={s}>
              <span>{s} {{ S1: '单轮', S2: '固定两轮', S3: '自适应' }[s]}</span>
              <div className="bar-track"><div className="bar-fill" style={{ width: `${(stats[s].correct / (stats[s].n || 1)) * 100}%` }} /></div>
              <b>{stats[s].correct}/{stats[s].n}</b>
            </div>
          ))}
        </div>
        <table className="metric-table">
          <thead><tr><th>指标</th><th>S1</th><th>S2</th><th>S3</th></tr></thead>
          <tbody>
            <tr><td>答案准确率</td><td>45%</td><td>60%</td><td>80%</td></tr>
            <tr><td>证据完整性</td><td>28.0%</td><td>46.2%</td><td>58.1%</td></tr>
            <tr><td>引用支持率</td><td>81.3%</td><td>90.6%</td><td>94.7%</td></tr>
            <tr><td>平均每题 tokens</td>{['S1', 'S2', 'S3'].map((s) => <td key={s}>{Math.round(stats[s].tokens).toLocaleString()}</td>)}</tr>
          </tbody>
        </table>
        <p className="lab-note">
          S3 平均消耗约为 S1 的 {ratio.toFixed(1)} 倍。逐题看，没有一道题是少轮策略答对而多轮策略答错。结论基于 20 道目的性构建的题目、单一模型、每格单次运行，属于描述性比较，不宜直接推广。
        </p>
        <button type="button" className="btn ghost" onClick={goLab}>在实验台上实时运行三种策略</button>
      </section>

      <section className="design-map">
        <h2>论文发现了什么，溯数怎么做</h2>
        <dl>
          {DESIGN.map(([finding, product]) => (
            <div key={finding} style={{ display: 'contents' }}>
              <dt>{finding}</dt>
              <dd>{product}</dd>
            </div>
          ))}
        </dl>
        <p className="lab-note">
          60 次运行的完整记录、逐轮证据和盲评结果可在论文附录 C 的回放站查看：
          <a href="https://xpiw-1.github.io/financerag-workbench/" target="_blank" rel="noreferrer">xpiw-1.github.io/financerag-workbench</a>
        </p>
      </section>
    </div>
  )
}
