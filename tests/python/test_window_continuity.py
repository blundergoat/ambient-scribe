"""Tests for the per-window speaker-continuity artifact builder.

The eval runner uses this script after a replay so a wrong Doctor/Patient row
can be traced to the emission window that produced it. These tests feed
Docker-style log lines without starting containers, keeping the seam
diagnostics verifiable in pytest.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def run_window_continuity(session_id: str, log_lines: str) -> list[dict]:
    """Run the artifact script over captured log text and parse its JSONL rows.

    Args:
        session_id: Browser recording UUID whose windows should be extracted.
        log_lines: Docker-style log text; empty still yields one summary row.

    Returns:
        Parsed JSONL rows in output order, summary row last.
    """
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts/window-continuity.py"), session_id],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
        input=log_lines,
    )
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def window_log_line(session_id: str, window_index: int, **fields: object) -> str:
    """Build one Docker-prefixed window-continuity JSON log line.

    Args:
        session_id: Recording UUID stamped on the event.
        window_index: Emission window number within the visit.
        **fields: Extra continuity fields; omitted ones read as unknown downstream.
    """
    event = {
        "ts": f"2026-07-05T07:00:0{window_index}Z",
        "event": (
            f"nemo_session.window_continuity session_id={session_id} "
            f"window_index={window_index} emitted_rows=1"
        ),
        "session_id": session_id,
        "window_index": window_index,
        **fields,
    }
    return f"nemo-agent-1  | {json.dumps(event)}"


def test_window_rows_are_scoped_to_one_session_and_summarized() -> None:
    """The artifact keeps one visit's windows and ends with honest totals."""
    session_id = "window-session"
    log_lines = "\n".join(
        [
            # A window from the visit under investigation.
            window_log_line(
                session_id,
                1,
                phase="chunk",
                emitted_from_seconds=0.0,
                emitted_until_seconds=2.0,
                raw_speaker_ids=["spk_0", "spk_1"],
                speaker_id_map={"spk_0": "spk_0", "spk_1": "spk_1"},
                mapping_reasons={"spk_0": "new_visible", "spk_1": "new_visible"},
                window_remaps=0,
                window_phantom_merges=0,
                emitted_rows=2,
                held_rows=0,
            ),
            # A remapped window: the diarizer swapped labels and the anchor fixed them.
            window_log_line(
                session_id,
                2,
                phase="chunk",
                emitted_from_seconds=2.0,
                emitted_until_seconds=3.5,
                raw_speaker_ids=["spk_0", "spk_1"],
                speaker_id_map={"spk_0": "spk_1", "spk_1": "spk_0"},
                mapping_reasons={
                    "spk_0": "overlap_vote",
                    "spk_1": "two_speaker_swap",
                },
                overlap_votes=[
                    {
                        "window_speaker_id": "spk_0",
                        "known_speaker_id": "spk_1",
                        "overlap_seconds": 0.5,
                    }
                ],
                window_remaps=2,
                window_phantom_merges=1,
                slot_share_evidence=[
                    {
                        "speaker_slot": "spk_2",
                        "window_voiced_frames": 3,
                        "cumulative_voiced_frames": 3,
                        "voiced_share": 0.02,
                        "fold_decision": "fold_marginal",
                    }
                ],
                folded_word_spans=[
                    {
                        "origin_speaker_slot": "spk_2",
                        "visible_speaker_slot": "spk_0",
                        "start_seconds": 10.1,
                        "end_seconds": 10.3,
                        "word_count": 2,
                    }
                ],
                emitted_rows=1,
                held_rows=1,
            ),
            # Another browser tab's window must stay out of this artifact.
            window_log_line("other-session", 1, emitted_rows=5),
            # Unrelated structured logs and console noise are skipped.
            'nemo-agent-1  | {"ts":"2026-07-05T07:00:03Z","event":"role_inference.completed","session_id":"window-session"}',
            "nemo-agent-1  | plain console text without json",
            "nemo-agent-1  | {broken json",
        ]
    )

    rows = run_window_continuity(session_id, log_lines)

    # Two windows for this visit plus the trailing summary row.
    assert len(rows) == 3
    first_window, remapped_window, summary = rows

    assert first_window["event"] == "nemo_session.window_continuity"
    assert first_window["window_index"] == 1
    assert first_window["emitted_rows"] == 2

    # The remapped window keeps the seam evidence a developer needs to see.
    assert remapped_window["speaker_id_map"] == {"spk_0": "spk_1", "spk_1": "spk_0"}
    assert remapped_window["mapping_reasons"]["spk_1"] == "two_speaker_swap"
    assert remapped_window["overlap_votes"][0]["overlap_seconds"] == 0.5
    assert remapped_window["slot_share_evidence"][0]["fold_decision"] == "fold_marginal"
    assert remapped_window["folded_word_spans"][0]["word_count"] == 2

    assert summary == {
        "event": "window_continuity.summary",
        "session_id": session_id,
        "window_count": 2,
        "emitted_rows": 3,
        "held_rows": 1,
        "windows_with_remaps": 1,
        "window_remaps": 2,
        "window_phantom_merges": 1,
    }


def test_empty_logs_still_produce_a_summary_row() -> None:
    """No captured windows still yields a summary so the eval warning can fire."""
    rows = run_window_continuity("quiet-session", "")

    assert rows == [
        {
            "event": "window_continuity.summary",
            "session_id": "quiet-session",
            "window_count": 0,
            "emitted_rows": 0,
            "held_rows": 0,
            "windows_with_remaps": 0,
            "window_remaps": 0,
            "window_phantom_merges": 0,
        }
    ]
