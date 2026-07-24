# Phase 1 Rollback

## Checkpoint

- Scoped backup: `/home/thenam176/.local/share/trading-agent-backups/phase-1-prechange-20260711.tgz`
- SHA-256: `fb34b191731d2519e31006d08bd73cd6d6eb17da85086cf3e421d5b0bc70e376`.
- Pre-runtime order/trade counts: 30 / 0.
- Pre-runtime mode: paper; kill switch: inactive.

## Runtime configuration rollback

1. Stop `trading-dashboard.service` and `trading-agent.service`.
2. Remove or restore the two `phase1-safety.conf` drop-ins from the checkpoint.
3. Restore protected environment files only from a restrictive backup; never copy them to Git.
4. Run `systemctl --user daemon-reload` and `systemd-analyze --user verify`.
5. Restore reviewed code patches or commits, then start the dashboard and verify port 3002.
6. Keep `.mode=paper`; verify `/api/meta` and the service log.

If rollback removes the two-gate policy or canonical kill-switch resolver, keep `trading-agent.service` stopped and make execution credentials unavailable until the safe patch is restored. Do not rely on the former dashboard kill-switch path.
