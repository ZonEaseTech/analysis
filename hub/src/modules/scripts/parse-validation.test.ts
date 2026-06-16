import { expect, test } from 'bun:test'
import { parseValidation } from './index'

test('returns null when no validator marker is present', () => {
  expect(parseValidation(['[hub] running', 'done'])).toBeNull()
})

test('parses a single marker into camelCase counts', () => {
  const logs = ['noise', '[[hub:validation]] {"total_rows": 100, "must_fix": 2, "needs_review": 5}']
  expect(parseValidation(logs)).toEqual({ totalRows: 100, mustFix: 2, needsReview: 5 })
})

test('aggregates multiple markers (one per sheet)', () => {
  const logs = [
    '[[hub:validation]] {"total_rows": 10, "must_fix": 1, "needs_review": 0}',
    '[[hub:validation]] {"total_rows": 20, "must_fix": 0, "needs_review": 3}',
  ]
  expect(parseValidation(logs)).toEqual({ totalRows: 30, mustFix: 1, needsReview: 3 })
})

test('ignores a malformed marker without throwing', () => {
  const logs = [
    '[[hub:validation]] not-json',
    '[[hub:validation]] {"total_rows": 5, "must_fix": 0, "needs_review": 0}',
  ]
  expect(parseValidation(logs)).toEqual({ totalRows: 5, mustFix: 0, needsReview: 0 })
})
