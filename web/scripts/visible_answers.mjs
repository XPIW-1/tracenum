// The answer text a visitor sees by default (after foldAnswer moves revision notes and
// verifier talk into 查看修正过程). Used by scripts/release_check.py so the check runs on
// exactly what the web app renders.
//   node web/scripts/visible_answers.mjs <answers.json>   (a JSON array of strings)
import { readFileSync } from 'node:fs'
import { foldAnswer } from '../src/answerRevision.js'

const answers = JSON.parse(readFileSync(process.argv[2], 'utf8'))
process.stdout.write(JSON.stringify(answers.map((a) => foldAnswer(a || '').answer)))
