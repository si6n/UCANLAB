"""Generate reproducible hash-locked requirements for supply-chain security (T62-U6)."""

from __future__ import annotations

import importlib.metadata
import json
import urllib.request

PACKAGES = [
    "python-can",
    "can-isotp",
    "cantools",
    "numpy",
    "scipy",
    "asammdf",
    "simplekml",
    "pandas",
    "zstandard",
    "pywebview",
    "pyinstaller",
    "cryptography",
    "pydantic",
    "pydantic-settings",
    "structlog",
    "python-dotenv",
]


def main() -> None:
    lines = [
        "# Reproducible hash-locked dependencies for Universal CAN-Bus Platform",
        "# Generated for supply-chain integrity (T62-U6 / pip install --require-hashes)",
        "",
    ]
    for pkg in PACKAGES:
        try:
            ver = importlib.metadata.version(pkg)
        except Exception as exc:
            print(f"Skipping {pkg}: {exc}")
            continue
        url = f"https://pypi.org/pypi/{pkg}/{ver}/json"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "universal-can-lock-gen/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                releases = data.get("urls", [])
                hashes = sorted(
                    list(
                        {
                            f"--hash=sha256:{u['digests']['sha256']}"
                            for u in releases
                            if "digests" in u and "sha256" in u["digests"]
                        }
                    )
                )
                if hashes:
                    lines.append(f"{pkg}=={ver} \\")
                    for i, h in enumerate(hashes):
                        suffix = " \\" if i < len(hashes) - 1 else ""
                        lines.append(f"    {h}{suffix}")
                    lines.append("")
                    print(f"Locked {pkg}=={ver} ({len(hashes)} hashes)")
        except Exception as exc:
            print(f"Error fetching {pkg}: {exc}")

    with open("requirements.lock", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("Done writing requirements.lock")


if __name__ == "__main__":
    main()
