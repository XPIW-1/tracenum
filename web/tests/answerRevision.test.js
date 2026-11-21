import assert from 'node:assert/strict'
import test from 'node:test'
import { foldAnswer, splitProcessNotes, splitRevision } from '../src/answerRevision.js'

test('folds consecutive leading revision paragraphs and keeps every character', () => {
  const revision = '\r\n**修正说明：** 上一版使用了 1,304.00 元。[第10页]\r\n\r\n核对结论：上一版取错了单位。\r\n\r\n'
  const answer = '本期为 **−1,304,000.00 元**。[第12页]\r\n\r\n复核结果：末尾说明留在正文。'
  assert.deepEqual(splitRevision(revision + answer), { answer, revision })
})

test('handles revision headings and an explicit answer on a following line', () => {
  const revision = '## 核对说明\n\n上一版漏引了[第80页]。\n\n复核结果：见年报原表。\n'
  const answer = '**最终答案：** 1,025 千元。[第80页]'
  assert.deepEqual(splitRevision(revision + answer), { answer, revision })
})

test('keeps ordinary answers, substantive conclusions, and revision-only outputs intact', () => {
  for (const answer of [
    '',
    '本期为 1,025 千元。\n\n修正说明：对照[第80页]。',
    '核对结论：2025年为 1,025 千元。\n\n依据[第80页]。',
    '修正说明：上一版引错页码。\n\n前一版引用[第81页]。\n\n',
  ]) assert.deepEqual(splitRevision(answer), { answer, revision: '' })
})

test('folds an 「已核对。上一版…」 opening but not a plain 「已核对。」 conclusion', () => {
  const revised = '已核对。上一版分项营业收入数值无误，但“三项合计校验”表述不当。\n\n动力电池 25,858,369,302.45 元 [第20页]'
  const r = splitRevision(revised)
  assert.equal(r.revision.trim(), '已核对。上一版分项营业收入数值无误，但“三项合计校验”表述不当。')
  assert.ok(r.answer.startsWith('动力电池'))
  assert.equal(r.revision + r.answer, revised)
  const plain = '已核对。营业收入为 25,858,369,302.45 元 [第20页]'
  assert.deepEqual(splitRevision(plain), { answer: plain, revision: '' })
})

test('folds only the sentences or clauses that argue with the verifier feedback', () => {
  const text = '2025年营业收入为 **13,632,727,136.01元**。[第101页]\n\n' +
    '因此原文支持 **777,102,455,000元**；外部反馈显示 `operation: NONE`，不能据此否定原文金额。\n\n' +
    '外部核验反馈为 `UNSUPPORTED`，未列示可核验操作数。'
  const { answer, revision } = foldAnswer(text)
  assert.ok(answer.includes('13,632,727,136.01元'))
  assert.ok(answer.includes('因此原文支持 **777,102,455,000元**；'))
  assert.doesNotMatch(answer, /UNSUPPORTED|NONE|外部/)
  assert.match(revision, /UNSUPPORTED/)
  assert.match(revision, /operation: NONE/)
})

test('never folds an answer into nothing', () => {
  const only = '外部核验反馈为 UNSUPPORTED。'
  assert.deepEqual(splitProcessNotes(only), { answer: only, notes: '' })
})
