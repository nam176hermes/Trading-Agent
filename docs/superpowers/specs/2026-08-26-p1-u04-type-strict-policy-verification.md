# P1-U04 type-strict policy verification design

Status: operator-approved bounded architecture-escalation packet, round 1/2.

## Problem

The committed Python and native-snapshot policies are intended to be exact,
but some verifier comparisons use Python `==`. Python equality treats booleans
as integers and equal-valued integers and floats as equal. Consequently JSON
mutations such as `false` for `0`, `true` for `1`, or `8020928.0` for
`8020928` can pass verification and be emitted into a regenerated manifest.

## Decision

Exact JSON policy comparisons must be recursive and type-strict. At every
node, the observed JSON value must have the same concrete JSON/Python type as
the reviewed expected value before values are compared. Dictionaries require
the exact key set and recursively exact values; lists require the exact length,
order, element types, and values.

This packet applies the invariant to:

- every exact compound or numeric field in the committed Python policy;
- the complete native snapshot binding, including integer schema version and
  all fourteen ordered mappings;
- the existing Python-to-snapshot coverage check, which remains independent
  and fail-closed before the complete snapshot binding validation.

Regression tests exercise the real full-policy route for representative
top-level and nested Python `bool`/`int` and `float`/`int` substitutions. A
separate native-snapshot test proves that boolean schema-version substitution
is rejected. Existing exact-value, no-live-`/usr`, receipt, payload, mapping,
and descriptor-rooted runtime checks remain unchanged in authority.

## Boundaries and circuit breaker

No build, network, package fallback, live `/usr` fallback, activation,
promotion, push, merge, deployment, or U05 work is authorized. Native Build A
remains locked until complete fresh X3 and X4 reviews PASS. This packet permits
at most two implementation/review rounds; a second-round material review
failure ends with `P1_U04_ARCHITECTURE_ESCALATION_REQUIRED`.
