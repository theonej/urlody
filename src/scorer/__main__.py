"""Entry point for ``python -m scorer`` and for the PyInstaller build."""

from scorer.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
