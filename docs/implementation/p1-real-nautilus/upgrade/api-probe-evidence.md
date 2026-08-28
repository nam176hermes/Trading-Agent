# P1-U05 API and callback qualification

Status: `PASS` on `NT1231-U04-G1`.

The sealed CPython 3.12 probe covered all 33 generated API surfaces and all 153
generated local invocation mappings. Two real execution-simulation cases
observed entry/accounting and entry/flatten behavior. The observed callbacks
were `on_start`, `on_bar`, and `on_order_filled`; `on_order_rejected` was not
observed and was not synthesized.

The engine lifecycle probe used explicit `load_state=false`, `save_state=false`,
`run_analysis=false`, and logging bypass, then observed reset retention and
dispose. Candidate closure identity and every manifest-bound file were identical
before and after qualification. The exact receipt is
`u05-api-qualification-receipt.json`.

This evidence does not activate or promote the candidate and grants no live,
network-trading, production, deployment, broker, or exchange authority.
