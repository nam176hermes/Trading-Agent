# P1-U04 immutable Python policy verification design

Status: operator-approved bounded architecture packet, round 1/2.

## Problem

The candidate native snapshot is immutable and receipt-bound, but the full
toolchain-input generator still calls `_verify_system_python()`. That function
reopens and hashes `/usr/bin/python3.12`, libpython, and `/usr/lib/python3.12`.
A valid sealed snapshot can therefore be rejected after unrelated live `/usr`
drift, and the current focused test misses the full `_verify_policies()` route.

## Decision

Full policy generation must validate the committed Python policy without any
filesystem access to its `/usr` paths. The validation is structural and exact:

- the existing CPython identity, executable, libpython, stdlib inventory,
  startup, path-admission, and external-link records remain exact;
- the native snapshot mapping must cover the executable, stdlib root, and the
  directory containing libpython;
- the already reviewed snapshot policy binds the exact receipt digest, payload
  digest, fixed root, and all fourteen mappings;
- real payload identity remains enforced by `verify_and_open()` in host/native
  qualification and at every candidate sandbox invocation.

The obsolete live verifier is not called from `_verify_policies()` or
`generate()`. A regression test exercises the complete `_verify_policies()`
route with the live verifier replaced by a fatal sentinel. Separate negative
tests mutate the Python-to-snapshot cross-binding and must fail closed.

## Boundaries and circuit breaker

No build, network, package fallback, live `/usr` fallback, activation,
promotion, push, merge, deployment, or U05 work is authorized. Native Build A
remains locked until fresh X3 and X4 reviews PASS. This packet permits at most
two implementation/review rounds; a second-round review failure ends with
`P1_U04_ARCHITECTURE_ESCALATION_REQUIRED`.
