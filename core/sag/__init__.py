"""sag — the shared statsataglance library.

Imported as `sag`, not `core`, deliberately (D11). `wbb-lab` imports this
package from a *different repo*, where `from core.espn import ...` reads as
"core of what?" — and a generic top-level `core` risks colliding with a
dependency shipping its own.

Installed with `pip install -e core/`, which installs a pointer to the source
rather than a copy, so edits are live without reinstalling. That editable
install is forced rather than chosen: the lab imports production and
cross-repo imports have no relative-path option (workspace-architecture §5).

> The lab imports production. Production never imports the lab.
"""

from sag.config import LeagueConfig

__all__ = ["LeagueConfig"]
