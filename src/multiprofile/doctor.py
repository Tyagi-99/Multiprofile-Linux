"""One command that answers "is this installation healthy, and if not why".

The parity roadmap asks for *"concise operator diagnostics for identity and
network failures"*. The information already existed, spread across four
places: `/api/health`'s identity faults, its permission faults, the identity
matrix, the engine verdict, and each profile's own route resolution. An
operator debugging a profile that will not load a page had to know which of
those to read, and in what order.

So this collects them, orders them by whether they are *blocking*, and prints
something short enough to paste into a report. It makes no network request of
its own and launches nothing: it reads what the product already knows.

**Redaction.** No proxy credentials, no IP addresses, no cookies. A profile's
proxy appears as scheme/host/port with the password absent, which is what
`ProxyConfig.masked_str()` already guarantees.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SEVERITY_BLOCKING = "blocking"   # a profile will not work until this is fixed
SEVERITY_WARNING = "warning"     # works, with a caveat worth knowing
SEVERITY_INFO = "info"


@dataclass
class Finding:
    severity: str
    area: str
    summary: str
    detail: str = ""
    profile_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity, "area": self.area,
            "summary": self.summary, "detail": self.detail,
            "profileId": self.profile_id,
        }


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == SEVERITY_BLOCKING]

    @property
    def ok(self) -> bool:
        return not self.blocking

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "facts": self.facts,
            "findings": [f.to_dict() for f in self.findings],
        }


def _engine(report: Report) -> None:
    from .engine_policy import VERDICT_SUPPORTED, verify_engine

    verdict = verify_engine()
    record = verdict.record
    report.facts["engine"] = {
        "version": record.version_full,
        "origin": record.origin,
        "ageDays": round(record.age_days, 1) if record.age_days is not None else None,
        "status": verdict.status,
    }
    if record.binary is None:
        report.findings.append(Finding(
            SEVERITY_BLOCKING, "engine",
            "no Chrome or Chromium was found",
            "Nothing can launch. Install Chromium, or set "
            "MULTIPROFILE_ENGINE_BINARY to a build."))
        return
    if verdict.pin_violated:
        report.findings.append(Finding(
            SEVERITY_BLOCKING, "engine", "the pinned engine was not used",
            verdict.reasons[0] if verdict.reasons else ""))
        return
    if verdict.status != VERDICT_SUPPORTED:
        report.findings.append(Finding(
            SEVERITY_WARNING, "engine",
            f"engine policy says {verdict.status}",
            verdict.reasons[0] if verdict.reasons else ""))


def _sandbox_and_secrets(report: Report) -> None:
    from .browser.launcher import find_chrome
    from .browser.sandbox import probe_sandbox
    from .secrets import status as secrets_status

    try:
        status = probe_sandbox(find_chrome())
        report.facts["sandbox"] = {"ok": status.ok, "reason": status.reason}
        if not status.ok:
            report.findings.append(Finding(
                SEVERITY_WARNING, "sandbox",
                "Chromium is running without its sandbox", status.reason))
    except Exception as e:  # noqa: BLE001 - reported, not swallowed
        report.findings.append(Finding(
            SEVERITY_WARNING, "sandbox", "the sandbox could not be probed", str(e)))

    try:
        health = secrets_status()
        report.facts["secrets"] = {"ok": bool(health.get("ok")),
                                   "backend": health.get("backend")}
        if not health.get("ok"):
            report.findings.append(Finding(
                SEVERITY_WARNING, "secrets",
                "proxy passwords are not protected by the OS keychain",
                str(health.get("message") or "")))
    except Exception as e:  # noqa: BLE001
        report.findings.append(Finding(
            SEVERITY_WARNING, "secrets", "secret storage could not be checked", str(e)))


def _faults(report: Report) -> None:
    from .browser import identity_faults
    from .storage.xdg import permission_faults

    identity = identity_faults.recent(limit=50)
    report.facts["identityFaults"] = len(identity)
    for fault in identity[:8]:
        report.findings.append(Finding(
            SEVERITY_WARNING, "identity", f"[{fault.code}] {fault.detail[:160]}",
            profile_id=getattr(fault, "profile_id", None)))

    permissions = permission_faults()
    report.facts["permissionFaults"] = len(permissions)
    for fault in permissions[:5]:
        report.findings.append(Finding(
            SEVERITY_WARNING, "permissions",
            f"could not make {fault.get('path')} owner-only",
            str(fault.get("detail") or "")))


def _identity_matrix(report: Report) -> None:
    from .fingerprint.identity_matrix import EXPERIMENTAL, matrix

    verdicts = [v for v in matrix()]
    report.facts["identityMatrix"] = {
        "supported": [v.claim for v in verdicts if v.ok],
        "experimental": [v.claim for v in verdicts if v.status == EXPERIMENTAL],
    }


def _routes(report: Report) -> None:
    """Each profile's route, resolved. The blocking case is the useful one."""
    from .network import network_config_for_profile, unsupported_route_reason
    from .storage.profiles import load_profiles

    try:
        profiles = load_profiles()
    except Exception as e:  # noqa: BLE001
        report.findings.append(Finding(
            SEVERITY_BLOCKING, "storage", "the profile store could not be read", str(e)))
        return

    report.facts["profiles"] = len(profiles)
    for profile in profiles:
        pid = profile.get("id")
        try:
            config = network_config_for_profile(dict(profile), commit=False)
        except Exception as e:  # noqa: BLE001
            report.findings.append(Finding(
                SEVERITY_BLOCKING, "network",
                "the route could not be resolved", str(e), profile_id=pid))
            continue
        if config.blocked_reason:
            report.findings.append(Finding(
                SEVERITY_BLOCKING, "network", "this profile will not launch",
                config.blocked_reason, profile_id=pid))
            continue
        unusable = unsupported_route_reason(config)
        if unusable:
            report.findings.append(Finding(
                SEVERITY_BLOCKING, "network",
                "this engine cannot use this route", unusable, profile_id=pid))


def run_doctor() -> Report:
    """Collect everything, in the order an operator would want it."""
    report = Report()
    for step in (_engine, _sandbox_and_secrets, _identity_matrix, _routes, _faults):
        try:
            step(report)
        except Exception as e:  # noqa: BLE001 - one bad check must not hide the rest
            report.findings.append(Finding(
                SEVERITY_WARNING, "doctor",
                f"the {step.__name__.strip('_')} check failed", str(e)))
    order = {SEVERITY_BLOCKING: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}
    report.findings.sort(key=lambda f: (order.get(f.severity, 9), f.area))
    return report


def format_report(report: Report) -> str:
    """Short enough to paste into an issue."""
    lines: list[str] = []
    facts = report.facts
    engine = facts.get("engine") or {}
    lines.append(
        f"engine    {engine.get('version') or 'NOT FOUND'} "
        f"({engine.get('origin')}, {engine.get('status')}"
        + (f", {engine.get('ageDays')}d old" if engine.get("ageDays") is not None else "")
        + ")"
    )
    sandbox = facts.get("sandbox") or {}
    lines.append(f"sandbox   {'ok' if sandbox.get('ok') else 'NOT SANDBOXED'}"
                 f" — {sandbox.get('reason', '')}")
    secrets = facts.get("secrets") or {}
    lines.append(f"secrets   {'encrypted' if secrets.get('ok') else 'UNPROTECTED'}"
                 f" ({secrets.get('backend')})")
    identity = facts.get("identityMatrix") or {}
    lines.append(f"identity  supported={','.join(identity.get('supported') or []) or '-'}"
                 f"  experimental={','.join(identity.get('experimental') or []) or '-'}")
    lines.append(f"profiles  {facts.get('profiles', '?')}"
                 f"  identity faults={facts.get('identityFaults', 0)}"
                 f"  permission faults={facts.get('permissionFaults', 0)}")

    blocking = report.blocking
    others = [f for f in report.findings if f.severity != SEVERITY_BLOCKING]
    lines.append("")
    if not blocking:
        lines.append("no blocking problems")
    else:
        lines.append(f"{len(blocking)} BLOCKING:")
        for finding in blocking:
            where = f" [{finding.profile_id}]" if finding.profile_id else ""
            lines.append(f"  - {finding.area}{where}: {finding.summary}")
            if finding.detail:
                lines.append(f"      {finding.detail[:200]}")
    if others:
        lines.append(f"{len(others)} warning(s):")
        for finding in others[:12]:
            where = f" [{finding.profile_id}]" if finding.profile_id else ""
            lines.append(f"  - {finding.area}{where}: {finding.summary[:150]}")
    return "\n".join(lines)
