"""``python -m rivermind`` entry point.

Delegates to the Click CLI so ``python -m rivermind <subcommand>`` works
identically to the ``rivermind`` console script installed via
``[project.scripts]``.
"""

from __future__ import annotations

from rivermind.cli import main

if __name__ == "__main__":
    main()
