# Phase 4B command manifest evidence

The final external command manifest SHA-256 is:

```text
5a1add2bd74454abfde59e99b4ee49d6c0d4564f2544e7e56749030764a99f78
```

It binds backend commit `41f055b48033714c660f44cc20498b7545366e75`, the
fixed `/opt` backend release, copied Python 3.11 interpreter, fixed cwd/argv,
code-owned timeouts and result validators. Client payloads cannot supply an
executable, module, shell, cwd, environment, output path or arbitrary argv.
Only SNAPSHOT is approved for a later controlled real-output rollout;
DEBATE, REPLAY and BACKTEST remain manual and were not run.

The protected runtime authority SHA-256 is:

```text
3c7437296e07ffa37c44678cb23769151a107403403a19737e6e661770932db8
```

It pins release manifests, command manifest, semantic policy, safety snapshot
path and exporter identity. The installer verifies both values against signed
staging metadata and exact stored bytes. No token, credential, raw payload or
environment secret is present. Root publication is pending the operator gate;
the worker remains stopped and cannot claim or spawn.

The old command digests `8cc075e8...` and `ec5efc82...`, and authority digests
`dcf7d0a0...` and `53311fba...`, belong to revoked stages and are not valid
installation authority.
