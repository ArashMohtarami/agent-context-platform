"""Deterministic, advisory safety checks for manual content publication.

The scanner deliberately reports *indicators*, rather than deciding that a
piece of prose is confidential or safe.  It never changes content, sends it to
a provider, or retains a matched value.  This keeps a useful last review step
from becoming either a publishing workflow or a second store of secrets.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from app.models.content import ContentItemDocument, ContentSourceReference

logger = logging.getLogger("agent_context.content.publication")


class PublicationFindingType(StrEnum):
    """The class of an explainable, non-secret-bearing review finding."""

    API_KEY = "api_key"
    CREDENTIAL = "credential"
    TOKEN = "token"  # noqa: S105 - an enum label, never a credential value.
    PRIVATE_URL = "private_url"
    INTERNAL_HOSTNAME = "internal_hostname"
    INTERNAL_NETWORK_ADDRESS = "internal_network_address"
    CUSTOMER_IDENTIFIER = "customer_identifier"
    PERSONALLY_IDENTIFIABLE_INFORMATION = "personally_identifiable_information"
    CONFIDENTIAL_PROJECT_REFERENCE = "confidential_project_reference"
    INTERNAL_REPOSITORY_URL = "internal_repository_url"
    SENSITIVE_ARCHITECTURE_REFERENCE = "sensitive_architecture_reference"


class PublicationFindingSeverity(StrEnum):
    """A policy may block high severity findings and retain lower ones for review."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PublicationFinding(BaseModel):
    """A location and explanation only; matched content is intentionally absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    finding_type: PublicationFindingType
    severity: PublicationFindingSeverity
    field: str = Field(min_length=1, max_length=64)
    location: str = Field(min_length=1, max_length=128)
    detail: str = Field(min_length=1, max_length=500)


class PublicationReview(BaseModel):
    """A safety-layer result, not a guarantee that content is safe to disclose."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str
    content_id: str
    publishable: bool
    findings: tuple[PublicationFinding, ...]
    project_ids: tuple[str, ...]
    source_refs: tuple[ContentSourceReference, ...]
    disclaimer: str = (
        "This deterministic review is a safety layer, not a guarantee that content is "
        "safe, complete, or non-confidential. Human review remains required."
    )


@dataclass(frozen=True, slots=True)
class PublicationSafetyPolicy:
    """Deployment-owned controls for a conservative, explainable scan."""

    block_high_severity: bool = True
    sensitive_terms: tuple[str, ...] = ()
    sensitive_project_ids: frozenset[str] = frozenset()


_URL = re.compile(r"https?://[^\s<>()\[\]{}\"']+", re.IGNORECASE)
_HOSTNAME = re.compile(
    r"(?<![A-Za-z0-9.-])(?:[A-Za-z0-9-]+\.)+(?:internal|local|localhost|corp|lan)(?![A-Za-z0-9.-])",
    re.IGNORECASE,
)
_IP_ADDRESS = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_INTERNAL_REPOSITORY = re.compile(
    r"(?:https?://|git@)[^\s/:@]+(?:\.[^\s/:@]+)*(?:[:/])[^\s]+?\.git\b",
    re.IGNORECASE,
)
_API_KEY = re.compile(
    r"(?:\b(?:api[_-]?key|x-api-key)\b\s*[:=]\s*[^\s\"'`]{8,}|"
    r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{20,}|\bAKIA[0-9A-Z]{16}\b|"
    r"\bgh[pousr]_[A-Za-z0-9]{20,}\b)",
    re.IGNORECASE,
)
_CREDENTIAL = re.compile(
    r"\b(?:password|passwd|client_secret|credential)\b\s*[:=]\s*[^\s\"'`]{6,}",
    re.IGNORECASE,
)
_TOKEN = re.compile(
    r"(?:\b(?:access|refresh|auth)[_-]?token\b\s*[:=]\s*[^\s\"'`]{8,}|"
    r"\bBearer\s+[A-Za-z0-9._~-]{12,})",
    re.IGNORECASE,
)
_CUSTOMER_IDENTIFIER = re.compile(
    r"\b(?:customer|tenant|account)[_-]?id\b\s*[:=]\s*[A-Za-z0-9_-]{3,}",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"\b[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\b")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d .()-]{7,}\d)(?!\w)")
_MAX_FINDINGS = 50


def review_content_for_publication(
    content: ContentItemDocument,
    *,
    policy: PublicationSafetyPolicy | None = None,
) -> PublicationReview:
    """Return redacted, deterministic indicators for one current content item.

    The returned data identifies a field and line/column, never a matching
    value.  Log fields use only identifiers, finding classes and counts for the
    same reason.
    """
    active_policy = policy or PublicationSafetyPolicy()
    findings: list[PublicationFinding] = []
    for field, value in _reviewable_fields(content):
        _scan_text(field, value, active_policy, findings)
        if len(findings) >= _MAX_FINDINGS:
            break
    _scan_sensitive_provenance(content, active_policy, findings)
    high_severity = any(item.severity is PublicationFindingSeverity.HIGH for item in findings)
    result = PublicationReview(
        profile_id=content.profile_id,
        content_id=content.content_id,
        publishable=not (active_policy.block_high_severity and high_severity),
        findings=tuple(findings[:_MAX_FINDINGS]),
        project_ids=tuple(content.project_ids),
        source_refs=tuple(content.source_refs),
    )
    logger.info(
        "content_publication_reviewed",
        extra={
            "profile_id": content.profile_id,
            "content_id": content.content_id,
            "publishable": result.publishable,
            "finding_types": tuple(
                sorted({finding.finding_type.value for finding in result.findings})
            ),
            "finding_count": len(result.findings),
        },
    )
    return result


def _reviewable_fields(content: ContentItemDocument) -> tuple[tuple[str, str], ...]:
    fields: list[tuple[str, str]] = [("title", content.title), ("body", content.body)]
    if content.external_url is not None:
        fields.append(("external_url", content.external_url))
    return tuple(fields)


def _scan_text(
    field: str,
    value: str,
    policy: PublicationSafetyPolicy,
    findings: list[PublicationFinding],
) -> None:
    url_spans = tuple(match.span() for match in _URL.finditer(value))
    _add_pattern_findings(
        findings,
        field,
        value,
        _API_KEY,
        PublicationFindingType.API_KEY,
        PublicationFindingSeverity.HIGH,
        "Potential API key detected; remove it before approval.",
    )
    _add_pattern_findings(
        findings,
        field,
        value,
        _CREDENTIAL,
        PublicationFindingType.CREDENTIAL,
        PublicationFindingSeverity.HIGH,
        "Potential credential detected; remove it before approval.",
    )
    _add_pattern_findings(
        findings,
        field,
        value,
        _TOKEN,
        PublicationFindingType.TOKEN,
        PublicationFindingSeverity.HIGH,
        "Potential access token detected; remove it before approval.",
    )
    _add_pattern_findings(
        findings,
        field,
        value,
        _CUSTOMER_IDENTIFIER,
        PublicationFindingType.CUSTOMER_IDENTIFIER,
        PublicationFindingSeverity.MEDIUM,
        "Potential customer identifier detected; confirm disclosure.",
    )
    _add_pattern_findings(
        findings,
        field,
        value,
        _EMAIL,
        PublicationFindingType.PERSONALLY_IDENTIFIABLE_INFORMATION,
        PublicationFindingSeverity.MEDIUM,
        "Potential email address detected; confirm disclosure.",
    )
    _add_pattern_findings(
        findings,
        field,
        value,
        _PHONE,
        PublicationFindingType.PERSONALLY_IDENTIFIABLE_INFORMATION,
        PublicationFindingSeverity.MEDIUM,
        "Potential phone number detected; confirm disclosure.",
    )
    for match in _URL.finditer(value):
        host = urlsplit(match.group()).hostname
        if host is not None and _is_private_host(host):
            _add_finding(
                findings,
                field,
                value,
                match.start(),
                PublicationFindingType.PRIVATE_URL,
                PublicationFindingSeverity.HIGH,
                "Private or internal URL detected; remove it before approval.",
            )
    for match in _INTERNAL_REPOSITORY.finditer(value):
        if _internal_repository(match.group()):
            _add_finding(
                findings,
                field,
                value,
                match.start(),
                PublicationFindingType.INTERNAL_REPOSITORY_URL,
                PublicationFindingSeverity.HIGH,
                "Internal repository URL detected; remove it before approval.",
            )
    for match in _HOSTNAME.finditer(value):
        if not _inside(match.start(), url_spans):
            _add_finding(
                findings,
                field,
                value,
                match.start(),
                PublicationFindingType.INTERNAL_HOSTNAME,
                PublicationFindingSeverity.HIGH,
                "Internal hostname detected; remove it before approval.",
            )
    for match in _IP_ADDRESS.finditer(value):
        if not _inside(match.start(), url_spans) and _is_private_ip(match.group()):
            _add_finding(
                findings,
                field,
                value,
                match.start(),
                PublicationFindingType.INTERNAL_NETWORK_ADDRESS,
                PublicationFindingSeverity.HIGH,
                "Internal network address detected; remove it before approval.",
            )
    for term in policy.sensitive_terms:
        if not term:
            continue
        term_match = re.search(re.escape(term), value, re.IGNORECASE)
        if term_match is not None:
            _add_finding(
                findings,
                field,
                value,
                term_match.start(),
                PublicationFindingType.SENSITIVE_ARCHITECTURE_REFERENCE,
                PublicationFindingSeverity.HIGH,
                "A deployment-configured sensitive architecture reference was detected.",
            )


def _scan_sensitive_provenance(
    content: ContentItemDocument,
    policy: PublicationSafetyPolicy,
    findings: list[PublicationFinding],
) -> None:
    if not policy.sensitive_project_ids:
        return
    project_ids = (*content.project_ids, *(ref.project_id for ref in content.source_refs))
    for index, project_id in enumerate(project_ids):
        if project_id is not None and project_id in policy.sensitive_project_ids:
            findings.append(
                PublicationFinding(
                    finding_type=PublicationFindingType.CONFIDENTIAL_PROJECT_REFERENCE,
                    severity=PublicationFindingSeverity.HIGH,
                    field="provenance",
                    location=f"project reference {index + 1}",
                    detail="Content is derived from a deployment-configured sensitive project.",
                )
            )


def _add_pattern_findings(
    findings: list[PublicationFinding],
    field: str,
    value: str,
    pattern: re.Pattern[str],
    finding_type: PublicationFindingType,
    severity: PublicationFindingSeverity,
    detail: str,
) -> None:
    for match in pattern.finditer(value):
        _add_finding(findings, field, value, match.start(), finding_type, severity, detail)


def _add_finding(
    findings: list[PublicationFinding],
    field: str,
    value: str,
    offset: int,
    finding_type: PublicationFindingType,
    severity: PublicationFindingSeverity,
    detail: str,
) -> None:
    if len(findings) >= _MAX_FINDINGS:
        return
    line = value.count("\n", 0, offset) + 1
    last_line_break = value.rfind("\n", 0, offset)
    column = offset + 1 if last_line_break < 0 else offset - last_line_break
    findings.append(
        PublicationFinding(
            finding_type=finding_type,
            severity=severity,
            field=field,
            location=f"line {line}, column {column}",
            detail=detail,
        )
    )


def _inside(offset: int, spans: tuple[tuple[int, int], ...]) -> bool:
    return any(start <= offset < end for start, end in spans)


def _is_private_host(host: str) -> bool:
    normalized = host.rstrip(".").lower()
    if normalized in {"localhost", "localhost.localdomain"}:
        return True
    if normalized.endswith((".internal", ".local", ".localhost", ".corp", ".lan")):
        return True
    return _is_private_ip(normalized)


def _is_private_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local


def _internal_repository(value: str) -> bool:
    candidate = value
    host: str | None
    if candidate.startswith("git@"):
        host = candidate[4:].split(":", 1)[0]
    else:
        host = urlsplit(candidate).hostname
    return host is not None and _is_private_host(host)
