"""Measurement tools that compare a profile against a control.

These exist because of a pattern this project keeps rediscovering: the
documents agreed with themselves and disagreed with the browser. Every
finding in ``docs/02_AUDIT_REPORT.md`` that mattered came from running
something, and the two found by a public checker on 5 October 2026 -- a
service worker reporting the host OS, and CSS reporting the real display --
were both in surfaces the internal probes did not cover.

So the unit of work here is a *manifest*: one structured, redacted
observation set, collected the same way from the product and from stock
Chromium on the same host in the same session, so a difference is attributable
to the product rather than to the environment.
"""
from .identity_probe import (
    REDACTED,
    ParityFinding,
    collect_manifest,
    compare_manifests,
    redact,
)

__all__ = [
    "ParityFinding",
    "REDACTED",
    "collect_manifest",
    "compare_manifests",
    "redact",
]
