const MAX_FAILURES = 5;
const WINDOW_MS = 15 * 60 * 1000;
const MAX_ENTRIES = 1_024;
const CLEANUP_BATCH_SIZE = 32;

interface LoginFailures {
  count: number;
  firstFailureAt: number;
}

export type LoginAttemptCheck =
  | { allowed: true }
  | { allowed: false; retryAfter: number };

const failures = new Map<string, LoginFailures>();

function cleanStaleEntries(now: number): void {
  let inspected = 0;
  for (const [key, entry] of failures) {
    if (now - entry.firstFailureAt >= WINDOW_MS) failures.delete(key);
    inspected += 1;
    if (inspected >= CLEANUP_BATCH_SIZE) break;
  }
}

function enforceEntryLimit(): void {
  while (failures.size > MAX_ENTRIES) {
    const oldestKey = failures.keys().next().value as string | undefined;
    if (oldestKey === undefined) return;
    failures.delete(oldestKey);
  }
}

export function checkLoginAttempt(key: string, now: number): LoginAttemptCheck {
  cleanStaleEntries(now);
  const entry = failures.get(key);
  if (!entry || entry.count < MAX_FAILURES) return { allowed: true };

  const remainingMs = WINDOW_MS - (now - entry.firstFailureAt);
  if (remainingMs <= 0) {
    failures.delete(key);
    return { allowed: true };
  }

  return { allowed: false, retryAfter: Math.ceil(remainingMs / 1000) };
}

export function recordLoginFailure(key: string, now: number): void {
  cleanStaleEntries(now);
  const existing = failures.get(key);
  if (existing && now - existing.firstFailureAt < WINDOW_MS) {
    existing.count += 1;
  } else {
    failures.set(key, { count: 1, firstFailureAt: now });
  }
  enforceEntryLimit();
}
