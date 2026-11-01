import os
import tempfile

# config.py reads the environment at import time, so this has to run before any
# backend module is imported.
os.environ.setdefault("JARVIS_DATA_DIR", tempfile.mkdtemp(prefix="jarvis-tests-"))
os.environ.setdefault("JARVIS_PORT", "8799")
