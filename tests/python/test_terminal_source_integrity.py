"""Prove a note can only be built from an attested whole-visit source.

These are the M02 contract tests for the consult-3.1 incident: the user
stopped a visit, the browser's wait timed out, and a note was generated from
a pre-terminal snapshot that omitted the spoken emergency instructions. Every
test here holds the server to the terminal-watermark contract - a browser
timeout may release waiting UI, but only backend attestation may authorize a
correction snapshot or a note source.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import api.role_inference_queue as role_queue
import api.server as api_server
from api import source_integrity
from api.server import app, lifecycle, sessions
from nemo_pipeline import NemoPipeline
from nemo_session import TranscriptionSession
from post_visit_correction import PostVisitCorrectionResult

_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "scribe"
    / "source-integrity-day3c01.json"
)

# Golden vectors pin the canonical identity serialization. Changing field
# order, float repr, NFC handling, or null encoding breaks every stored hash,
# so any intentional change requires a schema version bump plus new vectors.
_GOLDEN_ROWS = [
    {
        "segment_id": "seg-0001",
        "speaker_id": "speaker_0",
        "role": "DOCTOR",
        "start": 0.0,
        "end": 1.5,
        "text": "Héllo café",
        "confidence": 0.875,
        "is_interim": False,
        "revision": 2,
    },
    {
        "segment_id": "seg-0002",
        "speaker_id": "speaker_1",
        "start": 1.5,
        "end": 3.1,
        "text": "yes",
    },
    {
        "segment_id": "seg-0003",
        "speaker_id": "speaker_1",
        "role": "",
        "start": 3.1,
        "end": 0.1 + 0.2,
        "text": "we do",
        "is_interim": True,
    },
]
_GOLDEN_DIGEST = "6853cedee8574b165182ff7c163c507a0b3a927ea37cb07cc3cac509b3d8822b"


def _visit_rows(session_id: str) -> list[dict]:
    """Store a tiny two-speaker visit the way live streaming would.

    Returns:
        The stored rows read back from storage; never empty.
    """
    rows = [
        {
            "segment_id": "seg-0001",
            "speaker_id": "speaker_0",
            "text": "how can I help you",
            "start": 0.0,
            "end": 2.0,
        },
        {
            "segment_id": "seg-0002",
            "speaker_id": "speaker_1",
            "text": "my lips are swelling",
            "start": 2.0,
            "end": 4.0,
        },
        {
            "segment_id": "seg-0003",
            "speaker_id": "speaker_1",
            "text": "we do",
            "start": 4.0,
            "end": 4.6,
        },
    ]
    for row in rows:
        sessions.append_segment(session_id, row)
    return sessions.get_segments(session_id)


def _attest_terminal(session_id: str) -> source_integrity.TerminalWatermark:
    """Freeze the stored rows as the visit's terminal identity, like finalize does."""
    return source_integrity.record_terminal_watermark(
        session_id,
        sessions.get_segments(session_id),
        audio_seconds=4.6,
        trimmed_seconds=0.0,
        role_revision=0,
        role_settlement="settled",
    )


def _seed_retained_audio(session_id: str) -> None:
    """Give the session retained PCM so the correction route can run ASR."""
    transcription_session = TranscriptionSession(
        session_id,
        pipeline=NemoPipeline(),
        input_format="pcm",
    )
    transcription_session.buffer.append(b"\0\0" * 160)
    asyncio.run(lifecycle.register(session_id, transcription_session))


def _cleanup(session_id: str) -> None:
    """Remove every store this test touched so cases stay independent."""
    sessions.cleanup(session_id)
    source_integrity.discard_terminal_watermark(session_id)
    role_queue._discard_role_settlement_state(session_id)
    asyncio.run(lifecycle.destroy(session_id))


def _covering_correction_result() -> PostVisitCorrectionResult:
    """Corrected output spanning the whole visit, so coverage passes."""
    return PostVisitCorrectionResult(
        segments=[
            {
                "segment_id": "corrected-0001",
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "how can I help you my lips are swelling we do",
                "start": 0.0,
                "end": 4.6,
                "source": "post_visit_correction",
                "source_model": "test-model",
            }
        ],
        model_name="test-model",
        word_count=10,
    )


def _dropping_correction_result() -> PostVisitCorrectionResult:
    """Corrected output that silently loses the final 'we do' reversal row."""
    return PostVisitCorrectionResult(
        segments=[
            {
                "segment_id": "corrected-0001",
                "speaker_id": "speaker_0",
                "role": "DOCTOR",
                "text": "how can I help you my lips are swelling",
                "start": 0.0,
                "end": 4.0,
                "source": "post_visit_correction",
                "source_model": "test-model",
            }
        ],
        model_name="test-model",
        word_count=8,
    )


def test_canonical_identity_matches_golden_vectors_and_ignores_roles() -> None:
    """The frozen serialization reproduces its pinned digest exactly.

    Role labels are excluded on purpose: a clinician correcting a speaker
    label after the visit must not invalidate the transcript's identity.
    """
    assert source_integrity.canonical_rows_hash(_GOLDEN_ROWS) == _GOLDEN_DIGEST

    relabeled = [dict(row) for row in _GOLDEN_ROWS]
    relabeled[0]["role"] = "PATIENT"
    assert source_integrity.canonical_rows_hash(relabeled) == _GOLDEN_DIGEST

    reworded = [dict(row) for row in _GOLDEN_ROWS]
    reworded[2]["text"] = "we do not"
    assert source_integrity.canonical_rows_hash(reworded) != _GOLDEN_DIGEST


def test_coverage_catches_the_dropped_reversal_row_from_the_m01_fixture() -> None:
    """Full input with one silently lost row must be exactly one uncovered id.

    This is the consult-3.1 killer case: the corrected artifact looked
    complete by count and identity, yet the patient's "we do" (antihistamines
    became available) never reached it.
    """
    with open(_FIXTURE_PATH, encoding="utf-8") as fixture_file:
        fixture = json.load(fixture_file)

    live_rows = []
    # The lifecycle events reassemble the complete terminal transcript.
    for event in fixture["lifecycle_events"]:
        live_rows.extend(event.get("rows", []))

    full_rows = fixture["corrected_artifacts"]["full_terminal"]["rows"]
    dropped = fixture["corrected_artifacts"]["full_input_dropped_row_variant"]

    assert source_integrity.coverage_unaccounted_rows(live_rows, full_rows) == []
    unaccounted = source_integrity.coverage_unaccounted_rows(live_rows, dropped["rows"])
    assert unaccounted == [dropped["dropped_live_segment_id"]]


def test_correction_is_blocked_until_the_visit_is_terminal() -> None:
    """A correction request before finalization must not snapshot the visit.

    Example: the user's Stop wait expired while the backend was still
    processing queued audio - the browser retries once `finalized` arrives.
    """
    session_id = "00000000-0000-4000-8000-0000000000a1"
    client = TestClient(app)
    try:
        _visit_rows(session_id)
        response = client.post(f"/session/{session_id}/correction")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "blocked"
        assert payload["reason"] == "source_not_terminal"
        assert sessions.get_corrected_segments(session_id) == []
    finally:
        _cleanup(session_id)


def test_correction_rejects_a_transcript_that_no_longer_matches_terminal() -> None:
    """Rows added after finalization make the lineage stale, never a source."""
    session_id = "00000000-0000-4000-8000-0000000000a2"
    client = TestClient(app)
    try:
        _visit_rows(session_id)
        _attest_terminal(session_id)
        # A late row lands after the watermark - the consult-3.1 shape.
        sessions.append_segment(
            session_id,
            {
                "segment_id": "seg-0099",
                "speaker_id": "speaker_0",
                "text": "call 999 straight away",
                "start": 4.6,
                "end": 6.0,
            },
        )
        response = client.post(f"/session/{session_id}/correction")
        payload = response.json()
        assert payload["status"] == "blocked"
        assert payload["reason"] == "stale_lineage"
    finally:
        _cleanup(session_id)


def test_correction_that_loses_a_meaningful_row_is_blocked_and_not_stored() -> None:
    """Coverage failure discards the artifact: whole-visit-true or absent."""
    session_id = "00000000-0000-4000-8000-0000000000a3"
    client = TestClient(app)
    try:
        _visit_rows(session_id)
        _attest_terminal(session_id)
        _seed_retained_audio(session_id)
        with patch.object(
            api_server,
            "run_post_visit_correction",
            return_value=_dropping_correction_result(),
        ):
            response = client.post(f"/session/{session_id}/correction")
        payload = response.json()
        assert payload["status"] == "blocked"
        assert payload["reason"] == "unaccounted_meaningful_rows"
        # The partial artifact must never become the note's corrected source.
        assert sessions.get_corrected_segments(session_id) == []
    finally:
        _cleanup(session_id)


def test_attested_correction_then_summary_reports_whole_visit_corrected() -> None:
    """The happy path: terminal visit, covering correction, attested note."""
    session_id = "00000000-0000-4000-8000-0000000000a4"
    client = TestClient(app)
    try:
        _visit_rows(session_id)
        watermark = _attest_terminal(session_id)
        _seed_retained_audio(session_id)
        with patch.object(
            api_server,
            "run_post_visit_correction",
            return_value=_covering_correction_result(),
        ):
            correction = client.post(f"/session/{session_id}/correction").json()
        assert correction["status"] == "ready"
        assert correction["source_state"] == "whole_visit_corrected"
        assert correction["attestation_id"] == watermark.attestation_id

        # The summary route publishes via the app's HTTP client, which only
        # exists once startup has run - the context manager provides that.
        with (
            patch.object(
                api_server,
                "_run_summary_generation",
                return_value={"title": "", "sections": [], "key_points": ["ok"]},
            ),
            TestClient(app) as started_client,
        ):
            summary = started_client.post(f"/session/{session_id}/summary").json()
        assert summary["source_state"] == "whole_visit_corrected"
        assert summary["attestation_id"] == watermark.attestation_id
    finally:
        _cleanup(session_id)


def test_summary_is_blocked_without_a_terminal_watermark() -> None:
    """Asking for a note while the visit is still finalizing must fail closed."""
    session_id = "00000000-0000-4000-8000-0000000000a5"
    client = TestClient(app)
    try:
        _visit_rows(session_id)
        payload = client.post(f"/session/{session_id}/summary").json()
        assert payload["status"] == "blocked"
        assert payload["reason"] == "source_not_terminal"
    finally:
        _cleanup(session_id)


def test_summary_waits_for_a_correction_outcome_before_using_live_rows() -> None:
    """Terminal but uncorrected visits report correction_pending, not a note."""
    session_id = "00000000-0000-4000-8000-0000000000a6"
    client = TestClient(app)
    try:
        _visit_rows(session_id)
        _attest_terminal(session_id)
        payload = client.post(f"/session/{session_id}/summary").json()
        assert payload["status"] == "blocked"
        assert payload["reason"] == "correction_pending"
    finally:
        _cleanup(session_id)


def test_bounded_correction_failure_falls_back_to_attested_live_rows() -> None:
    """Expired audio yields a visible, review-required live-fallback note.

    Example: the clinician returns to summarize long after Stop; retained
    audio is gone (ADR-006 family), but the terminal live transcript is
    complete and attested, so an honestly-labelled draft is still possible.
    """
    session_id = "00000000-0000-4000-8000-0000000000a7"
    client = TestClient(app)
    try:
        _visit_rows(session_id)
        watermark = _attest_terminal(session_id)
        # No lifecycle session is registered, so correction sees expired audio.
        correction = client.post(f"/session/{session_id}/correction").json()
        assert correction["status"] == "unavailable"
        assert correction["source_state"] == "whole_visit_live_fallback"
        assert correction["fallback_reason"] == "retained_audio_unavailable"

        # Startup-managed client: the publish step needs the app HTTP client.
        with (
            patch.object(
                api_server,
                "_run_summary_generation",
                return_value={"title": "", "sections": [], "key_points": ["ok"]},
            ),
            TestClient(app) as started_client,
        ):
            summary = started_client.post(f"/session/{session_id}/summary").json()
        assert summary["source_state"] == "whole_visit_live_fallback"
        assert summary["fallback_reason"] == "retained_audio_unavailable"
        assert summary["attestation_id"] == watermark.attestation_id
    finally:
        _cleanup(session_id)


def test_over_limit_visits_get_no_shortened_note() -> None:
    """Silent truncation is refused; the transcript stays reviewable instead."""
    session_id = "00000000-0000-4000-8000-0000000000a8"
    client = TestClient(app)
    try:
        rows = _visit_rows(session_id)
        _attest_terminal(session_id)
        truncated_context = SimpleNamespace(
            complete_segments=rows,
            selected_segments=rows,
            transcript="…",
            source="corrected_segments",
            citation_segments=[],
            citation_source_index=None,
            transcript_truncated=True,
            original_transcript_chars=99999,
            kept_transcript_chars=100,
        )
        watermark = source_integrity.get_terminal_watermark(session_id)
        watermark.correction_status = "attested_corrected"
        with patch.object(
            api_server, "build_summary_context", return_value=truncated_context
        ):
            payload = client.post(f"/session/{session_id}/summary").json()
        assert payload["status"] == "blocked"
        assert payload["reason"] == "source_exceeds_note_limit"
    finally:
        _cleanup(session_id)


def test_role_settlement_closes_the_revision_and_rejects_late_results() -> None:
    """After settlement, a late role result cannot change the user's draft."""
    session_id = "00000000-0000-4000-8000-0000000000a9"
    try:
        # No queued role work means the visit settles immediately.
        settlement = asyncio.run(
            role_queue.wait_for_role_settlement(session_id, timeout_seconds=0.5)
        )
        assert settlement == "settled"
        assert session_id in role_queue._closed_role_revisions

        # A late inference for the closed visit must return before calling the
        # model or publishing anything the clinician could see.
        def _must_not_run(*_args: object) -> dict:
            raise AssertionError("late role inference ran after settlement")

        services = role_queue.RoleInferenceServices(
            sessions=sessions,
            lifecycle=lifecycle,
            publish_to_mercure=_must_not_run,
            run_role_inference=_must_not_run,
            mercure_event_ids={},
        )
        asyncio.run(
            role_queue._infer_and_publish_role_update(
                session_id, [{"speaker_id": "speaker_0", "text": "hi"}], services
            )
        )
    finally:
        _cleanup(session_id)


def test_role_settlement_freezes_when_queued_work_cannot_drain() -> None:
    """A stuck role queue must not stall finalize forever: it freezes instead."""
    session_id = "00000000-0000-4000-8000-0000000000aa"
    try:
        queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        queue.put_nowait([{"speaker_id": "speaker_0", "text": "hi"}])
        role_queue.role_inference_queues[session_id] = queue

        settlement = asyncio.run(
            role_queue.wait_for_role_settlement(session_id, timeout_seconds=0.3)
        )
        assert settlement == "failed_frozen"
        assert role_queue._closed_role_revisions[session_id] == 0
    finally:
        role_queue.role_inference_queues.pop(session_id, None)
        _cleanup(session_id)


def test_role_result_in_flight_at_close_cannot_relabel_the_frozen_visit() -> None:
    """A role model still running when settlement freezes must not relabel rows.

    Example: the clinician stops the visit while one role batch is mid-inference
    on a slow provider. Settlement times out at its bound, the note source
    freezes as failed_frozen, and the provider answers a minute later - that
    late answer must be rejected, not painted over an already copied draft.
    """
    session_id = "00000000-0000-4000-8000-0000000000ab"

    async def _drive() -> tuple[str, list[dict]]:
        _visit_rows(session_id)
        release_provider = threading.Event()

        def _slow_role_inference(*_args: object) -> dict:
            # Holds the worker "busy" until settlement has already frozen the visit.
            release_provider.wait(timeout=5.0)
            return {
                "path": "mapping",
                "mapping": {"speaker_0": "DOCTOR", "speaker_1": "PATIENT"},
            }

        def _must_not_publish(*_args: object, **_kwargs: object) -> bool:
            raise AssertionError(
                "a stale role result was published after settlement froze the visit"
            )

        services = role_queue.RoleInferenceServices(
            sessions=sessions,
            lifecycle=lifecycle,
            publish_to_mercure=_must_not_publish,
            run_role_inference=_slow_role_inference,
            mercure_event_ids={},
        )
        worker = asyncio.create_task(
            role_queue._infer_and_publish_role_update(
                session_id,
                [{"speaker_id": "speaker_0", "text": "how can I help you"}],
                services,
            )
        )
        # The freeze must land while the provider call is genuinely in flight.
        while session_id not in role_queue._busy_role_sessions:
            await asyncio.sleep(0.01)
        settlement = await role_queue.wait_for_role_settlement(
            session_id, timeout_seconds=0.2
        )
        release_provider.set()
        await worker
        return settlement, sessions.get_segments(session_id)

    try:
        settlement, rows_after = asyncio.run(_drive())
        assert settlement == "failed_frozen"
        # The frozen visit keeps its raw speaker labels and its closed revision;
        # the late mapping must change neither the rows nor the lineage.
        assert all(not row.get("role") for row in rows_after)
        assert role_queue.current_role_revision(session_id) == 0
    finally:
        _cleanup(session_id)


def test_stream_cleanup_grace_prefers_post_visit_retention_after_finalize():
    """A finalized visit's audio outlives the clinician's reading gap (M12).

    The on-demand Generate button makes finalize-to-click delay unbounded, so a
    finalized session keeps the long retention window; a socket that ended
    WITHOUT a terminal watermark keeps the short reconnect grace, because that
    timer exists for stream resumption, not post-visit reading time.
    """
    from api.streaming_session import _stream_cleanup_grace_seconds

    services = SimpleNamespace(
        reconnect_grace_seconds=30.0,
        post_visit_audio_retention_seconds=900.0,
    )
    session_id = "wm-grace-selection-test"
    source_integrity.discard_terminal_watermark(session_id)

    # No terminal watermark: mid-visit reconnect semantics are unchanged.
    assert _stream_cleanup_grace_seconds(session_id, services) == 30.0

    source_integrity.record_terminal_watermark(
        session_id,
        [{
            "segment_id": "s-1",
            "speaker_id": "spk_0",
            "text": "hello",
            "start": 0.0,
            "end": 1.0,
        }],
        audio_seconds=1.0,
        trimmed_seconds=0.0,
        role_revision=1,
        role_settlement="settled",
    )
    try:
        assert _stream_cleanup_grace_seconds(session_id, services) == 900.0
    finally:
        source_integrity.discard_terminal_watermark(session_id)
