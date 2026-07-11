"""Tests for storage-level features: edit-rate tracking (self-improvement signal) and
the refund idempotency/audit trail (security-critical — prevents double refunds)."""

import pytest

pytestmark = pytest.mark.asyncio


# ── Edit tracking (self-improvement) ──────────────────────────


async def test_untouched_reply_is_not_counted_as_edited(test_store):
    await test_store.log_edit("t1", "Your order shipped!", "Your order shipped!", category="order_status")
    stats = await test_store.get_edit_stats()
    assert stats["total_ai_drafts_sent"] == 1
    assert stats["edited_before_send"] == 0


async def test_heavily_rewritten_reply_is_counted_as_edited(test_store):
    await test_store.log_edit(
        "t2", "Sorry, no refunds allowed.",
        "I hear you — let's get this refund started right away, sorry for the trouble!",
        category="refund",
    )
    stats = await test_store.get_edit_stats()
    assert stats["edited_before_send"] == 1
    assert stats["by_category"]["refund"]["edit_rate"] == 1.0


async def test_edit_stats_break_down_by_category(test_store):
    await test_store.log_edit("t1", "A", "A", category="order_status")  # untouched
    await test_store.log_edit("t2", "B", "completely different text here", category="refund")  # edited
    await test_store.log_edit("t3", "C", "C", category="order_status")  # untouched

    stats = await test_store.get_edit_stats()
    assert stats["by_category"]["order_status"]["total"] == 2
    assert stats["by_category"]["order_status"]["edited"] == 0
    assert stats["by_category"]["refund"]["total"] == 1
    assert stats["by_category"]["refund"]["edited"] == 1
    assert stats["overall_edit_rate"] == round(1 / 3, 3)


async def test_edit_stats_with_no_data_returns_none_rate(test_store):
    stats = await test_store.get_edit_stats()
    assert stats["total_ai_drafts_sent"] == 0
    assert stats["overall_edit_rate"] is None


# ── Refund idempotency / audit trail (security-critical) ──────


async def test_refund_audit_lookup_returns_none_before_any_refund(test_store):
    result = await test_store.get_refund_audit("key-1")
    assert result is None


async def test_refund_audit_records_and_replays_successful_refund(test_store):
    await test_store.record_refund_audit(
        "key-1", ticket_id="t1", order_id="999", amount=25.0, reason="damaged",
        status="succeeded", shopify_response={"refund": {"id": 555}},
    )
    replay = await test_store.get_refund_audit("key-1")
    assert replay is not None
    assert replay["amount"] == 25.0
    assert replay["status"] == "succeeded"
    assert replay["shopify_response"]["refund"]["id"] == 555


async def test_refund_audit_records_failures_too(test_store):
    await test_store.record_refund_audit(
        "key-2", ticket_id="t2", order_id="999", amount=25.0, reason="damaged",
        status="failed", error="Shopify API timeout",
    )
    replay = await test_store.get_refund_audit("key-2")
    assert replay["status"] == "failed"
    assert replay["error"] == "Shopify API timeout"


async def test_different_idempotency_keys_are_independent(test_store):
    await test_store.record_refund_audit(
        "key-a", ticket_id="t1", order_id="1", amount=10.0, reason="r", status="succeeded"
    )
    assert await test_store.get_refund_audit("key-b") is None
    assert (await test_store.get_refund_audit("key-a"))["amount"] == 10.0


# ── Confidence calibration ─────────────────────────────────────


async def test_calibration_report_empty_when_no_data(test_store):
    report = await test_store.get_calibration_report()
    assert all(b["count"] == 0 for b in report["buckets"].values())


async def test_calibration_report_buckets_by_confidence_correctly(test_store):
    # Low confidence, heavily edited (expected — low confidence SHOULD get edited)
    await test_store.log_edit("t1", "draft", "totally different text", category="refund", confidence=0.4)
    # High confidence, untouched (expected — high confidence SHOULD be trustworthy)
    await test_store.log_edit("t2", "Your order shipped!", "Your order shipped!", category="shipping", confidence=0.92)

    report = await test_store.get_calibration_report()
    low_bucket = report["buckets"]["0.00-0.50"]
    high_bucket = report["buckets"]["0.90-1.00"]
    assert low_bucket["count"] == 1
    assert low_bucket["edit_rate"] == 1.0
    assert high_bucket["count"] == 1
    assert high_bucket["edit_rate"] == 0.0


async def test_calibration_report_flags_miscalibration(test_store):
    """If HIGH confidence drafts get edited just as often as low confidence ones, that's
    the exact pattern a senior engineer needs surfaced — this test proves the report
    actually surfaces it rather than averaging it away."""
    for i in range(3):
        await test_store.log_edit(f"hi{i}", "draft text", "completely rewritten reply", category="x", confidence=0.9)

    report = await test_store.get_calibration_report()
    high_bucket = report["buckets"]["0.90-1.00"]
    assert high_bucket["edit_rate"] == 1.0, "a 100% edit rate at 0.9 confidence must be visible, not hidden"
