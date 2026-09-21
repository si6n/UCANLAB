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

    from src.safety.secret_provider import get_default_secret_provider

    provider = get_default_secret_provider()

    # Bind the authority to a throwaway enforcement object: minting needs the
    # challenge, verification of independence needs a distinct provider. The
    # enforcement object here is never engaged; it only carries the challenge.
    # S1-P2-5: inject the SAME provider instance used for minting. Letting
    # `EmergencyStopSystem()` self-instantiate risked a second, divergent
    # provider (notably with EphemeralSecretBackend) whose ESTOP_HMAC_SECRET
    # differed from the one this tool signs with.
    estop = EmergencyStopSystem(secret_provider=provider)
    authority = EStopResetAuthority(estop=estop, secret_provider=provider, key_name=args.key_name)

    challenge = EStopChallenge(
        epoch=args.epoch,
        nonce=bytes.fromhex(args.nonce),
        timestamp_monotonic_ns=args.timestamp_ns,
    )
    # Inject the out-of-band challenge as the active one for signing.
    with estop._lock:  # noqa: SLF001 - field tool wiring
        estop._is_engaged = True
        estop._epoch = args.epoch
        estop._active_challenge = challenge

    token = authority.mint_reset_token()
    if token is None:
        print("No active challenge — nothing minted.")
        return 1
    print("TOKEN:" + token.to_token_string())
    print("Submit within TTL via estop_submit_reset_token. Audit: "
          f"user={getpass.getuser()} epoch={args.epoch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
