import assert from 'node:assert/strict';
import test from 'node:test';

import {
  parseJob,
  parseJobDetail,
  parseJobList,
} from '../src/lib/trading/job-api-contract.ts';

const NOW = '2026-07-12T12:00:00Z';
const canonicalJob = {
  job_id: 'job_123',
  job_type: 'SNAPSHOT',
  state: 'QUEUED',
  payload: { scope: 'default', requested_as_of: null },
  payload_fingerprint: 'a'.repeat(64),
  actor: { actor_type: 'OPERATOR', actor_id: 'dashboard-operator' },
  priority: 3,
  requested_at: NOW,
  updated_at: NOW,
  attempt_count: 0,
  reason_code: 'ENQUEUED',
  result_hash: null,
};

test('strict parser corpus accepts canonical list and detail responses', () => {
  assert.deepEqual(parseJobList({ items: [canonicalJob], limit: 50, offset: 0 }), {
    items: [canonicalJob], limit: 50, offset: 0,
  });
  assert.deepEqual(parseJobDetail({
    job: canonicalJob,
    attempts: [],
    events: [],
    artifacts: [],
  }), {
    job: canonicalJob,
    attempts: [],
    events: [],
    artifacts: [],
  });
});

test('strict parser corpus rejects extra keys and payload/type incoherence', () => {
  assert.equal(parseJob({ ...canonicalJob, debug: true }), null);
  assert.equal(parseJob({
    ...canonicalJob,
    job_type: 'DEBATE',
  }), null);
  assert.equal(parseJobList({ items: [canonicalJob], limit: 50, offset: 0, total: 1 }), null);
});

test('strict parser corpus retains array bounds', () => {
  assert.equal(parseJobList({
    items: Array.from({ length: 101 }, () => canonicalJob),
    limit: 100,
    offset: 0,
  }), null);
  assert.equal(parseJobDetail({
    job: canonicalJob,
    attempts: [],
    events: Array.from({ length: 1_001 }, () => ({})),
    artifacts: [],
  }), null);
});
