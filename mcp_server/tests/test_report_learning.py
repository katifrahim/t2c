"""Self-validating tests for the report_learning tool — the agent → developer
self-improvement channel. The tool is intentionally stateless (validate + ack;
the web backend does the Langfuse write), so these tests prove the contract:
every valid type is accepted, bad input is rejected, severity is normalized, and
the payload the web layer reads back is well-formed. Importing the server also
proves adding the tool didn't break the module (no PostHog/atexit regression).

No async plugin needed — the tool coroutine is driven with asyncio.run()."""
import asyncio
import json

import pytest

from src.t2c_mcp import report_learning, _LEARNING_TYPES, _LEARNING_SEVERITIES


def _call(**kwargs):
    return json.loads(asyncio.run(report_learning(**kwargs)))


def test_types_and_severities_are_the_expected_sets():
    assert _LEARNING_TYPES == {
        "missing_capability", "tool_doc_error", "tool_bug",
        "context_gap", "stale_context", "technique", "painpoint",
    }
    assert _LEARNING_SEVERITIES == {"low", "medium", "high"}


@pytest.mark.parametrize("ltype", sorted(_LEARNING_TYPES))
def test_every_valid_type_is_logged(ltype):
    out = _call(type=ltype, title="t", detail="d")
    assert out["status"] == "logged"
    assert out["type"] == ltype
    assert out["title"] == "t"


def test_unknown_type_is_rejected():
    out = _call(type="not_a_type", title="t", detail="d")
    assert out["status"] == "error"
    assert "Unknown type" in out["error"]


@pytest.mark.parametrize("sev", sorted(_LEARNING_SEVERITIES))
def test_valid_severities_accepted(sev):
    assert _call(type="technique", title="t", detail="d", severity=sev)["status"] == "logged"


def test_bad_severity_is_tolerated_not_fatal():
    # severity is a soft field — an odd value must not fail the report.
    assert _call(type="painpoint", title="t", detail="d", severity="urgent")["status"] == "logged"


def test_optional_fields_are_accepted():
    out = _call(
        type="tool_bug", title="extrude both fails", detail="both=True errors",
        suggestion="accept both", severity="high", tool="workplane_api",
        evidence="extrude(5, both=True) -> traceback",
    )
    assert out["status"] == "logged"
