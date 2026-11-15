"""PyInstaller's GUI-only entry point; the source CLI remains separate."""
from minecraft_playden.packaged_app import main

raise SystemExit(main())
