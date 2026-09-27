#!/usr/bin/env python3
"""Export only public RSS headlines for the static demo; no local ledger imports."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.research.market_news import MarketNewsService, MAX_STALE_SECONDS


def export_snapshot(output: Path) -> dict:
    snapshot = MarketNewsService().snapshot()
    if snapshot["status"] == "unavailable" and output.exists():
        try:
            previous = json.loads(output.read_text())
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(previous["fetched_at"].replace("Z", "+00:00"))).total_seconds()
            if 0 <= age <= MAX_STALE_SECONDS and previous.get("items"):
                # Retain only known public snapshot fields, never arbitrary file content.
                snapshot = {"status": "stale", "items": [{key: row[key] for key in ("id", "title", "url", "source", "published_at")} for row in previous["items"][:6]],
                            "fetched_at": previous["fetched_at"], "message": "News feeds are temporarily unavailable.", "feeds": snapshot["feeds"]}
        except (ValueError, KeyError, TypeError, OSError):
            pass
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n")
    return snapshot


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = export_snapshot(args.output)
    print(f"Market news: {result['status']}; {len(result['items'])} public headlines")
