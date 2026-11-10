"""Allows ``python -m vmsim ...``."""

import sys

from .cli import main

sys.exit(main())
