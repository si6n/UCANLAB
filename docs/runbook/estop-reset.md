# E-Stop Reset Runbook (R2-E1)

Latched E-Stop recovery is an **out-of-band cryptographic ceremony**.
No UI toggle, scenario switch, or simulator control ever clears a latched
E-Stop (AGENTS.md §2.5).

## Procedure (challenge TTL: 30 s default)

1. In the app, call `estop_request_challenge`. Copy the three fields:
   `epoch`, `nonce` (32 hex chars), `timestamp_monotonic_ns`.
2. On the authorization holder's machine (access to the reset secret store):
   ```bash
   python scripts/estop_reset_tool.py --epoch <EPOCH> --nonce <NONCE> \
       --timestamp-ns <TS>
   ```
   The script re-authenticates the OS user, signs the challenge with
   `EStopResetAuthority`, and prints `TOKEN:<...>` plus an audit line.
3. Within the TTL, submit in the app: `estop_submit_reset_token` with the
   printed token string.
4. Verify: `get_safety_state` shows the latch cleared; TX remains disarmed
   until an explicit operator `arm_tx`.

## Notes

- Tokens are single-use and bound to (epoch, nonce, timestamp, ESTOP_RESET).
  A replayed or expired token fails closed.
- A process restart clears the process-local latch (accepted trade-off,
  see SECURITY.md). The ceremony above is still required for any
  remote/JS-initiated recovery attestation.
- Every mint is audit-logged by the tool (OS user + epoch). Keep these
  lines with the workshop's service records.
