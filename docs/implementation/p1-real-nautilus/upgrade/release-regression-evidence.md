# P1-U06 release regression evidence

Status: `PASS` on `NT1231-U04-G1`.

The closed catalog classifies all 40 v1.228-v1.231 release items: 15 bind to
native scenarios, 14 are explicitly not used, and 11 terminate at an upstream
or previously qualified boundary. All eight source-owned scenarios ran in a
fresh Bubblewrap process and matched the independent Decimal oracle and strict
root result validator field-for-field.

Observed totals: zero panics, zero duplicate accounting facts, zero unexplained
PnL drift, zero unclassified release items, and zero working orders after each
isolated process exited. Candidate closure bytes were identical before and after
the campaign. Exact machine evidence is in
`u06-regression-qualification-receipt.json`.

This evidence does not activate or promote the candidate and grants no live,
network-trading, production, deployment, broker, or exchange authority.
