from __future__ import annotations

import sys
import os
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# Importing app.main constructs its module-level ASGI application. Keep that
# default app in a disposable store as well as explicitly configured fixtures.
# This must run before test-module imports, not in a later pytest fixture.
_default_app_data = tempfile.TemporaryDirectory(prefix="road2m-test-default-")
os.environ["ROAD2M_DATA_DIR"] = _default_app_data.name
os.environ["ROAD2M_ENABLE_MARKET_CONNECTORS"] = "0"
os.environ["ROAD2M_ENABLE_REDDIT_INTAKE"] = "0"
