"""Tests for the role timeline artifact builder.

The eval runner uses this script after a replay so developers can see why the
browser labels moved or stayed fixed. These tests feed Docker-style log lines
without starting containers, keeping role diagnostics verifiable in pytest.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_role_timeline_filters_session_and_summarizes_decisions() -> None:
    """A session artifact keeps only relevant role decisions and final mapping."""
    session_id = "timeline-session"
    other_session_id = "other-session"
    log_lines = "\n".join(
        [
            (
                'nemo-agent-1  | {"ts":"2026-07-05T00:00:00Z",'
                '"event":"role_inference.completed","session_id":"timeline-session",'
                '"mapping":{"speaker_0":"DOCTOR","speaker_1":"PATIENT"},'
                '"confidence":0.82,"flip_detected":false,"tool_invoked":true,'
                '"path":"tool","fallback":false}'
            ),
            (
                'nemo-agent-1  | {"ts":"2026-07-05T00:00:01Z",'
                '"event":"role_mapping.flip_suppressed speakers=speaker_0,speaker_1",'
                '"session_id":"timeline-session",'
                '"previous":{"speaker_0":"DOCTOR","speaker_1":"PATIENT"},'
                '"proposed":{"speaker_0":"PATIENT","speaker_1":"DOCTOR"},'
                '"confidence":0.84,"running_confidence":0.82,"pending_flip_count":1}'
            ),
            (
                'nemo-agent-1  | {"ts":"2026-07-05T00:00:02Z",'
                '"event":"role_mapping.flip_detected speakers=speaker_0,speaker_1",'
                '"session_id":"timeline-session",'
                '"previous":{"speaker_0":"DOCTOR","speaker_1":"PATIENT"},'
                '"current":{"speaker_0":"PATIENT","speaker_1":"DOCTOR"}}'
            ),
            (
                'nemo-agent-1  | {"ts":"2026-07-05T00:00:03Z",'
                '"event":"role_inference.completed","session_id":"timeline-session",'
                '"mapping":{"speaker_0":"PATIENT","speaker_1":"DOCTOR"},'
                '"confidence":0.88,"flip_detected":true,"tool_invoked":true,'
                '"path":"tool","fallback":false}'
            ),
            (
                'nemo-agent-1  | {"ts":"2026-07-05T00:00:04Z",'
                '"event":"role_inference.completed","session_id":"timeline-session",'
                '"mapping":{"speaker_0":"PATIENT","speaker_1":"DOCTOR"},'
                '"confidence":0.60,"flip_detected":false,"tool_invoked":false,'
                '"path":"heuristic","fallback":true}'
            ),
            (
                f'nemo-agent-1  | {{"event":"role_inference.completed",'
                f'"session_id":"{other_session_id}","mapping":{{"speaker_9":"DOCTOR"}}}}'
            ),
            "plain non-json docker noise",
        ]
    )

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts/role-timeline.py"), session_id],
        cwd=REPO_ROOT,
        input=log_lines,
        check=True,
        text=True,
        capture_output=True,
    )
    rows = [json.loads(line) for line in result.stdout.splitlines()]

    assert [row["event"] for row in rows] == [
        "role_inference.completed",
        "role_mapping.flip_suppressed",
        "role_mapping.flip_detected",
        "role_inference.completed",
        "role_inference.completed",
        "role_timeline.summary",
    ]
    assert [row["decision"] for row in rows[:-1]] == [
        "accepted_update",
        "suppressed_flip",
        "accepted_flip",
        "accepted_flip",
        "fallback",
    ]
    assert rows[-1] == {
        "event": "role_timeline.summary",
        "session_id": session_id,
        "final_mapping": {"speaker_0": "PATIENT", "speaker_1": "DOCTOR"},
        "role_calls": 3,
        "accepted_flips": 1,
        "suppressed_flips": 1,
        "fallback_events": 1,
        "truncation_events": 0,
    }


def test_role_timeline_quality_check_flags_post_snapshot_decisions(
    tmp_path: Path,
) -> None:
    """Flip decisions logged after the quality snapshot must surface as a mismatch.

    session.quality counters are snapshotted at disconnect while the role worker
    is still draining, so the artifact needs an explicit agreement row instead of
    silently trusting the quality record.
    """
    session_id = "timeline-session"
    log_lines = "\n".join(
        [
            (
                'nemo-agent-1  | {"ts":"2026-07-05T00:00:00.000000Z",'
                '"event":"role_inference.completed","session_id":"timeline-session",'
                '"mapping":{"speaker_0":"DOCTOR","speaker_1":"PATIENT"},'
                '"confidence":0.82,"flip_detected":false,"tool_invoked":true,'
                '"path":"tool","fallback":false}'
            ),
            (
                'nemo-agent-1  | {"ts":"2026-07-05T00:00:02.000000Z",'
                '"event":"role_mapping.flip_suppressed speakers=speaker_0,speaker_1",'
                '"session_id":"timeline-session",'
                '"previous":{"speaker_0":"DOCTOR","speaker_1":"PATIENT"},'
                '"proposed":{"speaker_0":"PATIENT","speaker_1":"DOCTOR"},'
                '"confidence":0.84,"running_confidence":0.82,"pending_flip_count":1}'
            ),
            (
                'nemo-agent-1  | {"ts":"2026-07-05T00:00:03.000000Z",'
                '"event":"role_inference.completed","session_id":"timeline-session",'
                '"mapping":{"speaker_0":"DOCTOR","speaker_1":"PATIENT"},'
                '"confidence":0.83,"flip_detected":false,"tool_invoked":true,'
                '"path":"tool","fallback":false}'
            ),
        ]
    )
    quality_path = tmp_path / "quality.json"
    quality_path.write_text(
        json.dumps(
            {
                "role_flips_accepted": 0,
                "role_flips_suppressed": 0,
                "finalized_at": "2026-07-05T00:00:01.000000Z",
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/role-timeline.py"),
            "--quality-json",
            str(quality_path),
            session_id,
        ],
        cwd=REPO_ROOT,
        input=log_lines,
        check=True,
        text=True,
        capture_output=True,
    )
    rows = [json.loads(line) for line in result.stdout.splitlines()]

    assert rows[-2]["event"] == "role_timeline.summary"
    assert rows[-1] == {
        "event": "role_timeline.quality_check",
        "session_id": session_id,
        "quality_flips_accepted": 0,
        "quality_flips_suppressed": 0,
        "timeline_flips_accepted": 0,
        "timeline_flips_suppressed": 1,
        "flip_counts_match": False,
        "role_events_after_quality_snapshot": 2,
        "quality_finalized_at": "2026-07-05T00:00:01.000000Z",
    }
