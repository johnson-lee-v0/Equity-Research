#!/usr/bin/env python3
"""Import the local disclosure dataset into the unified app's private backup tree."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.config import settings
from backend.app.research.lab_data import import_archive
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', type=Path, default=settings.project_root/'exp/congress-lab')
parser.add_argument('--target', type=Path, default=settings.evidence_dir/'labs/congress')
parser.add_argument('--refresh', action='store_true', help='Install a new generation while preserving the previous dataset')
args=parser.parse_args()
result=import_archive(args.source,args.target,refresh=args.refresh)
print(json.dumps({k:v for k,v in result.items() if k not in ('sourceHashes','politicians')}, indent=2))
