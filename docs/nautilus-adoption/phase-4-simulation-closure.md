# Phase 4 semantic simulation closure

## Scope

Packet 04E1 adds a deterministic execution-simulation implementation to the
isolated CPython 3.12 launcher. It does not add public-data acquisition,
network clients, provider or broker configuration, databases, arbitrary
strategy modules, writable outputs, a persistent paper engine, or live
execution.

This source-stabilization packet does not materialize or select an external
closure. The current 04E0 root result validator intentionally accepts the
earlier two-digest transport event; Task 4 must add the reviewed expanded-result
contract before its execution-parity candidate can pass root result validation.
Until then, the boundary fails closed.

## Closed scenario profile

The semantic identity is `nautilus-execution-simulation-v2`. Its canonical,
hash-bound scenario grammar accepts exactly these identifiers:

- `long-accounting`
- `short-accounting`
- `partial-fill`
- `same-bar-stop-take-profit`
- `stale-quote`
- `zero-liquidity`
- `session-boundary`
- `event-digest`

The scenario binds the exact catalog, market-data, and strategy bytes. Events
must be ordered, fall inside the command window, match the catalog rows, and
use the fixed BTCUSDT instrument. All prices, quantities, fees, and P&L are
canonical finite decimal strings, bounded to 38 significant digits and a
bounded exponent before arithmetic. Price-like values must not exceed
`17,014,118,346,046`; quantity/capacity values must not exceed
`34,028,236,692,093`. Every derived operation runs under an isolated
precision-80 Decimal context. The launcher rejects floats, duplicate or unknown
keys, unbound inputs, excessive instrument precision, out-of-range fixed-point
values, unknown stop/take-profit precedence, arbitrary modules,
provider/execution settings, and output paths before Nautilus setup.

For a long same-bar scenario, the stop and take-profit triggers are valid only
when `stop_price < executable_entry_price < take_profit_price`; executable entry
includes declared slippage. This prevents ambiguous or reversed trigger causes
before Nautilus setup.

The sealed engine ingests a finite in-memory bar feed. The bounded Decimal-only
execution model applies explicit session-open, quote-age, liquidity,
fee/slippage, partial-fill, and stop-first same-bar rules. It emits exactly one
canonical completion line containing the five-input digest, scenario and event
digests, execution counters, position state, average entry, fees, realised and
unrealised P&L, and the declared precedence.

## External generations

No external closure files are tracked by Git.

| Generation | Disposition | Manifest SHA-256 | Attested closure SHA-256 |
| --- | --- | --- | --- |
| `runtime-closure-v3` | zero-order rollback | `69cb87568361ccd6324550fb3823956c64e073b4cf09e674d7eb0883f844c044` | `18c9ba4af073ae953e0115f577423348b6d454c158da59cbcbd3c9e34a22856f` |
| `runtime-closure-v4-simulation` through `v7` | rejected forensic candidates | `60fa9da972a1bb967f1117318b56e513301e3868536d8efb7f09284b02e5459c` | `f6080176c8a2c742a4f60a92be07a2e9078edaef97572e33c54c4431dd646cf2` |
| `runtime-closure-v8-simulation` | transport-only rollback | `60fa9da972a1bb967f1117318b56e513301e3868536d8efb7f09284b02e5459c` | `f6080176c8a2c742a4f60a92be07a2e9078edaef97572e33c54c4431dd646cf2` |
| `runtime-closure-v9-simulation` | rejected precision candidate | `2920be39c588d52c54dee735f6bc9dc6507b7650572fa7dcd8a9da84981e90a5` | `30397513e1b4342b4182dfbc143a3b24426e7186f690172bacc0ed7ec9598345` |
| `runtime-closure-v10-simulation` | rejected pre-grammar-repair candidate | `f2f16b61f46db2ca86b16dc47a13633a9b3aee3c74ed4ecca084e7c453990d0d` | `fed156160b837b75564de15e50236441851376ea67ac3bc60e58a911c81cd386` |
| `runtime-closure-v11-simulation` | rejected fixed-point/trigger-order candidate | `a0a327420767fc2ad52bb731b1297b9c6ea85d3965bbf53523cd17ca393b6ceb` | `ce70493ede59e19aeda7496da553ea97f7c9265d91ba95bf8a2b73e295de6597` |
| `runtime-closure-v12-simulation` | reserved for Task 4 real execution-parity candidate | — | — |

V9 passed closure attestation but failed the first real semantic smoke because
canonical whole-number prices were passed to Nautilus without the fixed
instrument precision. It remains immutable forensic evidence. V10 remains
rejected because independent review found scenario-ID ambiguity, ambient
Decimal-context dependence, and a policy-only semantic label. V11 is also
immutable forensic evidence: its preserved digests document the candidate, but
its absent fixed-point and trigger-order checks prohibit its selection. V8
remains transport-only rollback. V12 is reserved for Task 4 and has not been
materialized.

## Qualification evidence

- Source launcher SHA-256:
  `e8012838ba6eca788de98d6520123f769602d28f8f57662395b2e0d54b3dab8f`.
- Source runtime-closure policy SHA-256:
  `cf44792684f720cf6cda42f6de86bf7aadd10abbc437efa112fc37c7952aa740`.
- Selected artifact manifest SHA-256:
  `105579383ea3c5e44104bbe162ab78380f7abb5654e15ac3b600beee54ed93d2`.
- The source tests prove v2 rejects reversed/equal long trigger order and the
  two exact fixed-point overflows before engine setup. They also prove a v1
  semantic manifest cannot be selected under v2.
- No external closure was materialized, attested, or executed by this packet.
  Historical v11 observations remain forensic only and are not qualification
  evidence for v2 or Task 4.
- V3–v11 inode identities and manifest digests remain preserved; v8 remains
  transport-only rollback and v12 is reserved.

These checks prove a bounded offline semantic fixture. They do not authorize
service installation or startup, a scheduler, provider or broker access,
paper-runtime activation, database mutation, or live trading.
