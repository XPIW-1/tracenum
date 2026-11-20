const META_HEADING = /^(?:#{1,6}[ \t]+)?(?:\*\*|__)?(修正说明|核对与修正说明|核对说明|核对结论|复核结果)(?:\*\*|__)?(?:[ \t]*[：:]|(?=\s|$))(?:\*\*|__)?[ \t]*/
const PREVIOUS_DRAFT = /上一版|前一版|原答案|外部核验反馈|外部反馈/
const PREVIOUS_DRAFT_START = /^(?:\*\*|__)?(?:上一版|前一版|原答案|外部核验反馈|外部反馈)/
// e.g. 「已核对。上一版分项数值无误，但……」: only a revision note when it also refers to the draft.
const CHECKED_OPENING = /^(?:\*\*|__)?(?:已核对|经核对|复核后|核对后)(?:\*\*|__)?[。，,；;：:]/
const ANSWER_LINE = /^[ \t]*(?:#{1,6}[ \t]+)?(?:\*\*|__)?(?:答案|最终答案|最终回答|修正后(?:的)?答案)(?:\*\*|__)?(?:[ \t]*[：:]|[ \t]*$)/m

export function splitRevision(text) {
  const unchanged = { answer: text, revision: '' }
  const leading = text.match(/^\s*/)[0]
  const chunks = text.slice(leading.length).split(/(\r?\n[ \t]*(?:\r?\n[ \t]*)+)/)
  let i = 0
  for (; i < chunks.length; i += 2) {
    const block = chunks[i]
    const heading = block.match(META_HEADING)
    if (heading) {
      // A «核对结论» can be the answer itself, so require a reference to the draft or feedback.
      if (heading[1] === '核对结论' && !PREVIOUS_DRAFT.test(block)) break
    } else if (CHECKED_OPENING.test(block)) {
      if (!PREVIOUS_DRAFT.test(block)) break
    } else if (i === 0 || !PREVIOUS_DRAFT_START.test(block)) break

    const answerAt = block.search(ANSWER_LINE)
    if (answerAt > 0) {
      return {
        answer: block.slice(answerAt) + chunks.slice(i + 1).join(''),
        revision: leading + chunks.slice(0, i).join('') + block.slice(0, answerAt),
      }
    }
  }
  const answer = chunks.slice(i).join('')
  if (!i || !answer.trim()) return unchanged
  return { answer, revision: leading + chunks.slice(0, i).join('') }
}
