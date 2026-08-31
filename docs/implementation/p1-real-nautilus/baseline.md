# P1 product implementation baseline

Status: `P1-00_ACCEPTED`

P1 product work starts from the accepted parent source below. The remote main
receipt remains separate and no runtime, live, network-trading or production
authority follows from this source baseline.

| Identity | Value |
|---|---|
| Remote canonical main | `30b6017b07f1533d8d55abfbebec735c7f03f9e5` |
| Remote canonical tree | `fa6c3caa15d44d59b5ff08b10c1e6a0a4a20633a` |
| P1 accepted parent | `242f5f1be3a28cbb4241caacb03f82abed073bea` |
| P1 accepted parent tree | `9f8ba02822d54d1b4d6ba605a41a9e3d903f1c48` |
| P1 engine baseline receipt SHA-256 | `b1dfa25502c05cfa6daf529e106e24f0f5d6e25ca33af6ecaca3e1ba1528019b` |
| Candidate generation | `NT1231-U04-G1` |
| Candidate generation SHA-256 | `2ea31eaca9cf19715fe2a73abc8c3d11c7731466e6e84e50e65db4979be46f8c` |
| Candidate closure SHA-256 | `24f12b58cb0aba145e6d56146a71be874c5d9b214e7426eead9711131eaf1255` |
| Product target | Nautilus `1.231.0`, schema 8 |
| Legacy Phase4 | Nautilus `1.227.0`, schema 6, unchanged |

Implementation topology is the existing external worktree
`trading-agent-worktrees/p1-v4-rebaseline-30b6017` on branch
`codex/p1-v4-rebaseline-30b6017`. No nested or repository-local worktree is
created. Every subsequent P1 receipt names its accepted parent commit/tree.

## Tool authority

| Tool | Exact identity |
|---|---|
| Root Python | `Python 3.11.15` |
| uv | `0.11.7` |
| Sealed engine Python | `CPython 3.12.3` |
| Bubblewrap | `0.9.0` |
| Candidate Rust | `rustc 1.97.1 (8bab26f4f 2026-07-14)` |
| Candidate Cargo | `cargo 1.97.1 (c980f4866 2026-06-30)` |
| Node | `v22.23.0` |
| npm | `10.9.8` |

## Baseline checks

- `make check-p0-baseline`: PASS on `242f5f1…`.
- `make check-p0-maintainability`: PASS on `242f5f1…`.
- `make check-contracts`: PASS on `242f5f1…`.
- `make test-all`: not runnable from a linked worktree because the canonical
  audit correctly returns `E_ROOT: .git`; this is an authority/topology
  limitation, not a source PASS. Foundation supplies portable authority after
  publication.
- Candidate activation, live, network-trading and production authority: false.
