"""Heliopause: a local view of your agents."""

__version__ = "0.7.2"  # x-release-please-version

import sys

# WHY: macOS python3 caches compiled code in ~/Library/Caches/com.apple.python (sys.pycache_prefix).
# Heliopause promises no trace outside the lab, so nothing it imports after this line is cached.
sys.dont_write_bytecode = True
