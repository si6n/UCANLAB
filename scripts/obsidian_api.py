#!/usr/bin/env python3
"""Obsidian Local REST API client (optional, local-only).

Reads credentials exclusively from environment variables:
  OBSIDIAN_API_URL  (default: http://127.0.0.1:27123)
  OBSIDIAN_API_KEY  (required — never hardcode, never commit)
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

API_URL = os.getenv("OBSIDIAN_API_URL", "http://127.0.0.1:27123")
API_KEY = os.getenv("OBSIDIAN_API_KEY", "")
VAULT = os.getenv(
    "OBSIDIAN_VAULT_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "obsidian-vault"),
)


def api(ep: str, method: str = "GET", data: str | None = None) -> tuple[int, str]:
    if not API_KEY:
        return 0, "OBSIDIAN_API_KEY is not set (export it; never commit secrets)"
    url = f"{API_URL.rstrip('/')}/{ep.lstrip('/')}"
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "text/markdown; charset=utf-8"}
    req = urllib.request.Request(url, data=data.encode("utf-8") if data else None, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


def main() -> int:
    p = argparse.ArgumentParser(description="Obsidian REST API")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p_read = sub.add_parser("read")
    p_read.add_argument("path")
    p_write = sub.add_parser("write")
    p_write.add_argument("path")
    p_write.add_argument("text")
    p_append = sub.add_parser("append")
    p_append.add_argument("path")
    p_append.add_argument("text")
    p_search = sub.add_parser("search")
    p_search.add_argument("query")
    p_rep = sub.add_parser("report")
    p_rep.add_argument("title")
    p_rep.add_argument("summary")
    p_rep.add_argument("--role", default="assistant", help="Specialist role: safety, data, dev, research, etc.")
    args = p.parse_args()

    if args.cmd == "status":
        s, b = api("/")
        print(f"Status: {s}\n{b}")
        return 0 if s == 200 else 1
    if args.cmd == "read":
        s, b = api(f"/vault/{args.path.strip('/')}")
        print(b if s == 200 else f"Err {s}: {b}")
        return 0 if s == 200 else 1
    if args.cmd in ("write", "append"):
        m = "PUT" if args.cmd == "write" else "POST"
        s, b = api(f"/vault/{args.path.strip('/')}", method=m, data=args.text)
        print("OK" if s in (200, 204) else f"Err {s}: {b}")
        return 0 if s in (200, 204) else 1
    if args.cmd == "search":
        s, b = api(f"/search/simple/?query={urllib.parse.quote(args.query)}", method="POST")
        if s == 200:
            for it in json.loads(b)[:5]:
                print(f"- {it.get('filename')}")
        else:
            print(f"Err {s}: {b}")
        return 0 if s == 200 else 1
    if args.cmd == "report":
        d = datetime.date.today().isoformat()
        slug = args.title.lower().replace(" ", "-")
        path = f"04-Ajan-Notlari/{d}-{slug}.md"
        tpl = os.path.join(VAULT, "templates", "session-report.md")
        if not os.path.exists(tpl):
            tpl = os.path.join(VAULT, "templates", "orca-session.md")  # legacy fallback
        body = open(tpl, "r", encoding="utf-8").read() if os.path.exists(tpl) else "# {{title}}\n"
        body = body.replace("{{title}}", args.title).replace("{{date}}", d).replace("{{rol}}", args.role)
        body = body.replace("rol: orca #", f"rol: {args.role} #").replace("rol: assistant #", f"rol: {args.role} #") + f"\n\n## 📝 Özet\n{args.summary}\n"
        s, _ = api(f"/vault/{path}", method="PUT", data=body)
        if s not in (200, 204):
            os.makedirs(os.path.join(VAULT, "04-Ajan-Notlari"), exist_ok=True)
            open(os.path.join(VAULT, path), "w", encoding="utf-8").write(body)
        print(f"Rapor oluşturuldu: {path}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
