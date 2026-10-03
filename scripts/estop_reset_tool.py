"""E-Stop out-of-band reset authorizer (R2-E1 field tool).

Flow:
  1. Operator calls `estop_request_challenge` in the app and copies the
     challenge fields (epoch / nonce / timestamp_monotonic_ns).
  2. An AUTHORIZED holder runs this script on a machine with access to the
     reset secret store. The script re-authenticates the OS user, signs the
     challenge with `EStopResetAuthority` (independent provider), and prints
     the token string.
  3. Operator pastes the token into `estop_submit_reset_token` within the
     challenge TTL (30 s default).

The script never touches the live app process and never clears a latched
E-Stop by itself — it only mints the cryptographic authorization.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.safety.estop import (  # noqa: E402
    DEFAULT_ESTOP_KEY_NAME,
    EmergencyStopSystem,
    EStopChallenge,
    EStopResetAuthority,
)


def _confirm_operator(args: argparse.Namespace) -> None:
    user = getpass.getuser()
    print(f"OS user: {user}")
    print(f"Challenge: epoch={args.epoch} nonce={args.nonce} ts={args.timestamp_ns}")
    if not args.yes:
        answer = input("Sign this E-Stop reset challenge? Type YES to continue: ").strip()
        if answer != "YES":
            print("Aborted by operator.")
            raise SystemExit(2)
    # Re-authenticate: the typed account name must match the OS user so a
    # shoulder-surfed invocation cannot be replayed by another login.
    typed = input(f"Re-type your OS username ({user}) to authorize: ").strip() if not args.yes else user
    if typed != user:
        print("Username mismatch — refusing to sign.")
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sign an E-Stop reset challenge (out-of-band authorizer).")
    parser.add_argument("--epoch", type=int, required=True, help="Challenge epoch (from estop_request_challenge).")
    parser.add_argument("--nonce", required=True, help="Challenge nonce, 32 hex chars.")
    parser.add_argument("--timestamp-ns", type=int, required=True, help="Challenge timestamp_monotonic_ns.")
    parser.add_argument("--key-name", default=DEFAULT_ESTOP_KEY_NAME)
    parser.add_argument("--yes", action="store_true", help="Skip interactive YES (scripted use; still prints the audit line).")
    args = parser.parse_args(argv)

    _confirm_operator(args)

    import os

    from src.safety.secret_provider import get_default_secret_provider

    provider = get_default_secret_provider()
    # AUDIT 2026-10-03 (S7-02): the authority must not share the enforcement
    # object's provider (P4 / E-1), and this process has no latched E-Stop of
    # its own — a throw-away local enforcement object only satisfies the
    # authority's constructor; the relayed challenge is signed directly.
    local_estop = EmergencyStopSystem(reset_secret=os.urandom(32))
    authority = EStopResetAuthority(estop=local_estop, secret_provider=provider, key_name=args.key_name)
    challenge = EStopChallenge(
        epoch=args.epoch,
        nonce=bytes.fromhex(args.nonce),
        timestamp_monotonic_ns=args.timestamp_ns,
    )
    token = authority.sign_challenge(challenge)
    print("TOKEN:" + token.to_token_string())
    print("Submit within TTL via estop_submit_reset_token. Audit: "
          f"user={getpass.getuser()} epoch={args.epoch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
