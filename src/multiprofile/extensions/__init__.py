"""Extension management: import, assign to profiles, apply, verify.

The package is deliberately split so that no single module can both write a
file and claim an installation:

``capability``  what the installed browser will actually do. ``--load-extension``
                was removed from branded Google Chrome 137+, so the answer is
                binary-specific and measured, never assumed.
``package``     untrusted bytes in, validated payload out. Path traversal,
                symlinks, zip bombs, ambiguous roots and missing referenced
                files are all refused here.
``identity``    the extension ID, predicted from the managed path and then
                verified against what the browser reports.
``store``       persistence. Package state and per-profile state are separate
                tables so a query cannot confuse "a file exists" with
                "a profile has it installed".
``manager``     the state machine, bulk operations and per-profile results.
"""
from . import capability, identity, manager, package, store  # noqa: F401

__all__ = ["capability", "identity", "manager", "package", "store"]
