#!/usr/bin/env python3
"""UCANLAB ajan durum ozeti — cron icin ham veri toplayici.

LLM kullanmaz. Cikti, ajanin prompt'una enjekte edilir; ajan Turkce rapor yazar.
Amac: 'tum ajanlar ne yapiyor' sorusuna ucuz ve dogru cevap.
"""
from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime
from pathlib import Path

MAIN = Path(r"C:\Users\canak\Desktop\Universal-CAN-BUS-Tool")
HERMES = r"C:\Users\canak\AppData\Local\hermes\hermes-agent\venv\Scripts\hermes.exe"
BOARD = "ucanlab"


def hermes(*a: str) -> str:
    try:
        r = subprocess.run([HERMES, "kanban", "--board", BOARD, *a],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=90)
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return f"ERR {e}"


def load(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def main() -> int:
    now = time.time()
    out = [f"# UCANLAB AJAN DURUM OZETI — {datetime.now():%Y-%m-%d %H:%M}"]

    # --- Kanban gorevleri ---
    try:
        tasks = json.loads(hermes("ls", "--json"))
    except Exception:
        tasks = []

    from collections import Counter
    st = Counter(t.get("status") for t in tasks)
    out.append("\n## GOREV SAYILARI\n" +
               " | ".join(f"{k}={v}" for k, v in sorted(st.items())))

    by_agent = Counter()
    for t in tasks:
        if t.get("status") in ("running", "ready", "todo", "scheduled", "blocked"):
            by_agent[t.get("assignee")] += 1
    out.append("\n## AKTIF (ajan basina)\n" +
               (" ".join(f"{k}={v}" for k, v in by_agent.most_common()) or "hicbiri"))

    out.append("\n## AKTIF GOREVLER")
    for t in tasks:
        if t.get("status") in ("running", "ready", "todo", "scheduled", "blocked"):
            out.append(f"- [{t['status']}] @{t.get('assignee')}: {str(t.get('title'))[:85]}")

    # Son 3 saatte biten
    cutoff = now - 3 * 3600
    done_recent = []
    for t in tasks:
        if t.get("status") == "done":
            ts = t.get("completed_at") or 0
            if isinstance(ts, (int, float)) and ts > cutoff:
                done_recent.append(t)
    if done_recent:
        out.append(f"\n## SON 3 SAATTE BITEN ({len(done_recent)})")
        for t in done_recent[-12:]:
            out.append(f"- @{t.get('assignee')}: {str(t.get('title'))[:80]}")

    # --- Pipeline / kuyruk ---
    q = load(MAIN / "scripts" / "scan_queue.json", {})
    src = q.get("sources", [])
    pend = [s for s in src if s.get("status") == "pending"]
    done = [s for s in src if s.get("status") == "done"]
    out.append(f"\n## TARAMA KUYRUGU\n- kaynak: {len(src)} | pending={len(pend)} | done={len(done)}")
    if pend:
        out.append("- bekleyen: " + ", ".join(s["domain"] for s in pend[:8]))
    cr = q.get("credits", {})
    for k, v in cr.items():
        if isinstance(v, dict) and "remaining" in v:
            out.append(f"- kredi {k}: {v['remaining']} ({v.get('zone')})")

    pst = load(MAIN / "scripts" / ".pipeline_state.json", {})
    if pst.get("last_discovery"):
        out.append(f"- son kesif: {pst['last_discovery'][:16]}")

    # --- Son 30 dk degisen dosyalar (canli is kaniti) ---
    recent = []
    for base in [MAIN / "spn_gap_hunter" / "output", MAIN / "scripts", MAIN / "data"]:
        if not base.exists():
            continue
        for f in base.rglob("*"):
            try:
                if f.is_file() and now - f.stat().st_mtime < 1800:
                    recent.append((f.stat().st_mtime, f))
            except OSError:
                pass
    recent.sort(reverse=True)
    out.append(f"\n## SON 30 DK DOSYA AKTIVITESI ({len(recent)} dosya)")
    for mt, f in recent[:10]:
        out.append(f"- {int((now-mt)/60)}dk once: {f.name}")

    # --- Son commitler ---
    try:
        r = subprocess.run(["git", "log", "--format=%h %ad %s", "--date=format:%H:%M",
                            "-6", "--since=6 hours ago"], cwd=MAIN,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
        out.append("\n## SON 6 SAATTEKI COMMITLER\n" + (r.stdout.strip() or "(yok)"))
    except Exception:
        pass

    # --- Takili gorevler (uzun suredir running) ---
    stuck = []
    for t in tasks:
        if t.get("status") == "running":
            s = t.get("started_at") or 0
            if isinstance(s, (int, float)) and s and now - s > 3600:
                stuck.append((int((now-s)/60), t))
    if stuck:
        out.append("\n## UYARI: UZUN SUREN GOREVLER (>1 saat)")
        for mins, t in stuck:
            out.append(f"- {mins}dk @{t.get('assignee')}: {str(t.get('title'))[:70]}")

    # --- Raporu vault'a da yaz (kullanici Obsidian'dan gorebilsin) ---
    try:
        vdir = MAIN / "obsidian-vault" / "04-Ajan-Notlari"
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / "Ajan-Durum-Son.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    except Exception:
        pass

    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
