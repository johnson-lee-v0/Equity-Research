#!/usr/bin/env python3
"""Retain an original research desk SQLite database in the unified workspace."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.config import Settings
from backend.app.research.library import import_desk

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    print(json.dumps(import_desk(args.database.expanduser(), Settings().evidence_dir)))
