#!/usr/bin/env python3
"""Measure the eval-only ceiling from separated PriMock57 speaker channels.

Use this for M17 Phase 2 when the mixed-mono transcript loses words during
cross-talk. The script downloads the original doctor/patient channel WAVs into
a gitignored quality run, normalizes each channel to the browser WAV contract,
streams each channel through the live WebSocket path, then scores the combined
history with `scripts/transcript-quality.py`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import subprocess
import sys
import time
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import httpx
import websockets

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "audio"
MANIFEST_PATH = FIXTURE_DIR / "generated-manifest.json"
QUALITY_ROOT = REPO_ROOT / "var" / "quality" / "channel-ceiling"
TRANSCRIPT_QUALITY_SCRIPT = REPO_ROOT / "scripts" / "transcript-quality.py"
PRIMOCK57_MEDIA_BASE_URL = (
    "https://media.githubusercontent.com/media/babylonhealth/primock57/main/"
)
CHANNELS_BY_ROLE = {"DOCTOR": "doctor", "PATIENT": "patient"}
PERCENT_LINE_PATTERN = re.compile(r"^(.+?):\s+([0-9.]+)%")


@dataclass(frozen=True)
class FixtureChannelSource:
    """One PriMock57 mixed fixture and its separated source channels.

    The mixed WAV is what the browser replays today. The doctor/patient URLs
    are eval-only inputs used to measure how much accuracy could improve if
    overlap speech was no longer mixed into one mono stream.

    Attributes:
        fixture_name: Fixture stem shown in eval reports.
        mixed_wav_path: Browser replay WAV used for duration and TextGrid pairing.
        source_urls_by_role: Doctor/patient channel download URLs.
    """

    fixture_name: str
    mixed_wav_path: Path
    source_urls_by_role: dict[str, str]


@dataclass(frozen=True)
class ChannelCeilingResult:
    """Score summary for one eval-only separated-channel run.

    Each result is written beside the raw score artifacts so M17 can compare
    mixed-mono WER against the best practical channel-separated transcript.
    Empty metric values mean the scorer output did not include that line.

    Attributes:
        fixture_name: Fixture stem shown in eval reports.
        cutoff_seconds: Mixed replay duration used for fair scoring.
        history_path: Combined channel history JSON written for review.
        score_path: Transcript-quality report written for review.
        word_error_rate_percent: Overall WER, or None when unavailable.
        clean_word_error_rate_percent: Non-overlap WER, or None when unavailable.
        overlap_word_error_rate_percent: Overlap WER, or None when unavailable.
        recall_percent: Reference vocabulary recall, or None when unavailable.
        length_ratio: Hypothesis/reference length ratio, or None when unavailable.
    """

    fixture_name: str
    cutoff_seconds: float
    history_path: Path
    score_path: Path
    word_error_rate_percent: float | None
    clean_word_error_rate_percent: float | None
    overlap_word_error_rate_percent: float | None
    recall_percent: float | None
    length_ratio: float | None


def parse_args() -> argparse.Namespace:
    """Parse CLI options for one separated-channel eval run.

    Returns:
        Parsed arguments; empty fixture selection is rejected before scoring starts.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixtures", nargs="*", help="Fixture stem or consultation number")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Request every manifest fixture; requires --allow-unstable-full-run",
    )
    parser.add_argument(
        "--allow-unstable-full-run",
        action="store_true",
        help="Allow --all even though separated-channel full runs can destabilize NeMo",
    )
    parser.add_argument(
        "--agent-http-url",
        default="http://localhost:48101",
        help="FastAPI base URL used to fetch in-memory session history",
    )
    parser.add_argument(
        "--agent-ws-url",
        default="ws://localhost:48101",
        help="FastAPI WebSocket base URL for /ws/transcribe/{session_id}",
    )
    parser.add_argument(
        "--chunk-ms",
        type=int,
        default=5000,
        help="PCM chunk size in milliseconds, matching scripts/eval-fixtures.sh",
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=None,
        help="Artifact directory; default is var/quality/channel-ceiling/<timestamp>",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="HTTP timeout seconds per channel transcription",
    )
    return parser.parse_args()


def load_fixture_sources(manifest_path: Path = MANIFEST_PATH) -> list[FixtureChannelSource]:
    """Load PriMock57 source-channel metadata from the local fixture manifest.

    Args:
        manifest_path: Generated manifest path; missing means channel eval cannot locate sources.

    Returns:
        Fixture source rows; empty means no PriMock57 WAVs are available for scoring.
    """
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sources: list[FixtureChannelSource] = []

    # Each manifest entry describes one replay WAV the user can select in the UI.
    for entry in manifest:
        filename = str(entry.get("filename", ""))
        mixed_wav_path = FIXTURE_DIR / filename
        source_audio = entry.get("source_audio", [])

        # Non-PriMock or incomplete rows cannot produce separated-channel ceilings.
        if not filename.startswith("primock57-") or not isinstance(source_audio, list):
            continue

        source_urls_by_role = source_urls_from_manifest_entry(source_audio)

        # Missing role URLs mean the row cannot be scored safely.
        if set(source_urls_by_role) != set(CHANNELS_BY_ROLE):
            continue

        sources.append(
            FixtureChannelSource(
                fixture_name=Path(filename).stem,
                mixed_wav_path=mixed_wav_path,
                source_urls_by_role=source_urls_by_role,
            )
        )

    return sources


def source_urls_from_manifest_entry(source_audio: list[object]) -> dict[str, str]:
    """Map manifest source paths to DOCTOR/PATIENT download URLs.

    Args:
        source_audio: Manifest `source_audio` rows; empty means no channel downloads.

    Returns:
        Role-keyed media URLs; empty means the manifest entry is not channel separated.
    """
    source_urls_by_role: dict[str, str] = {}

    # Each source path is named like `audio/day1_consultation02_doctor.wav`.
    for source_path_value in source_audio:
        source_path = str(source_path_value)

        # Doctor and patient filenames declare which role that channel represents.
        for role, channel_name in CHANNELS_BY_ROLE.items():
            if source_path.endswith(f"_{channel_name}.wav"):
                source_urls_by_role[role] = urljoin(
                    PRIMOCK57_MEDIA_BASE_URL,
                    source_path,
                )

    return source_urls_by_role


def select_fixture_sources(
    sources: list[FixtureChannelSource],
    fixture_queries: list[str],
    include_all: bool,
) -> list[FixtureChannelSource]:
    """Choose which fixtures to score from the manifest source list.

    Args:
        sources: All manifest-backed channel sources; empty means no eval inputs exist.
        fixture_queries: User-entered stems or consultation numbers; empty means the eval
            did not name a safe single fixture.
        include_all: True means score every source row regardless of query text.

    Returns:
        Selected source rows in manifest order.

    Raises:
        ValueError: When selection is empty or a query matches zero or multiple fixtures.
    """
    # `--all` is the only path that intentionally asks for the unstable full ceiling table.
    if include_all:
        return sources

    # Empty fixture names would accidentally repeat the full run that destabilized NeMo.
    if not fixture_queries:
        raise ValueError("pass fixture queries, or use --all with --allow-unstable-full-run")

    selected_sources: list[FixtureChannelSource] = []

    # Each query should match exactly one fixture so score rows are reproducible.
    for query in fixture_queries:
        matches = [
            source for source in sources if query in source.fixture_name
        ]

        # A typo should fail loudly instead of silently omitting a fixture.
        if not matches:
            raise ValueError(f"no fixture matched query {query!r}")

        # Ambiguous short queries would make the ceiling table misleading.
        if len(matches) > 1:
            matched_names = ", ".join(source.fixture_name for source in matches)
            raise ValueError(f"fixture query {query!r} matched multiple rows: {matched_names}")

        selected_sources.append(matches[0])

    return selected_sources


def fixture_cutoff_seconds(mixed_wav_path: Path) -> float:
    """Read the mixed fixture duration used as the scorer cutoff.

    Args:
        mixed_wav_path: Browser replay WAV; missing means no comparable mixed baseline exists.

    Returns:
        Duration in seconds; zero means the WAV cannot be scored.
    """
    with wave.open(str(mixed_wav_path), "rb") as wav_file:
        return wav_file.getnframes() / wav_file.getframerate()


def download_channel(source_url: str, destination_path: Path) -> None:
    """Download one PriMock57 source channel into the eval artifact directory.

    Args:
        source_url: Public media URL; empty means the role channel cannot be fetched.
        destination_path: Artifact path; parent directories are created when missing.
    """
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", source_url, follow_redirects=True, timeout=120.0) as response:
        response.raise_for_status()
        with destination_path.open("wb") as output_file:
            # The WAV can be large, so chunks keep memory stable during full corpus evals.
            for chunk in response.iter_bytes():
                # Empty keepalive chunks do not add audio for the evaluator.
                if chunk:
                    output_file.write(chunk)


def normalize_channel_wav(source_path: Path, destination_path: Path) -> None:
    """Convert one source channel to 16 kHz mono PCM WAV for NeMo batch scoring.

    Args:
        source_path: Downloaded source WAV; missing means the channel download failed.
        destination_path: Normalized WAV path passed to `/transcribe/file`.

    Raises:
        RuntimeError: When FFmpeg is unavailable and the browser audio contract cannot be enforced.
        subprocess.CalledProcessError: When FFmpeg rejects the source channel audio.
    """
    ffmpeg_path = shutil.which("ffmpeg")

    # Missing FFmpeg means the eval cannot enforce the browser audio contract.
    if ffmpeg_path is None:
        raise RuntimeError("ffmpeg is required for channel-ceiling normalization")

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg_path,
            "-y",
            "-i",
            str(source_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-sample_fmt",
            "s16",
            str(destination_path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def transcribe_channel(
    wav_path: Path,
    session_id: str,
    agent_http_url: str,
    agent_ws_url: str,
    chunk_ms: int,
    timeout_seconds: float,
) -> dict[str, object]:
    """Stream one normalized channel WAV and return its stored history.

    Args:
        wav_path: Normalized WAV path for one role channel.
        session_id: Eval session ID shown in server logs.
        agent_http_url: FastAPI base URL; empty means no history endpoint can be called.
        agent_ws_url: FastAPI WebSocket URL; empty means no live stream can be opened.
        chunk_ms: Browser-like PCM chunk size; zero would send no useful audio.
        timeout_seconds: History timeout; zero means only one immediate fetch is attempted.

    Returns:
        History payload; empty segments mean NeMo produced no text for the channel.
    """
    asyncio.run(stream_wav_to_websocket(wav_path, session_id, agent_ws_url, chunk_ms))
    return fetch_session_history(agent_http_url, session_id, timeout_seconds)


async def stream_wav_to_websocket(
    wav_path: Path,
    session_id: str,
    agent_ws_url: str,
    chunk_ms: int,
) -> None:
    """Send one normalized WAV over the same WebSocket path as the browser.

    Args:
        wav_path: 16 kHz mono PCM WAV; invalid format means the browser contract is broken.
        session_id: Valid UUID accepted by the route.
        agent_ws_url: WebSocket base URL for the Python agent.
        chunk_ms: Chunk duration in milliseconds; zero would send no frames.

    Raises:
        RuntimeError: When the normalized channel WAV violates the browser audio contract.
    """
    with wave.open(str(wav_path), "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        channel_count = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        total_frames = wav_file.getnframes()

        # The evaluator uses the browser PCM contract so GPU behavior matches product replay.
        if sample_rate != 16000 or channel_count != 1 or sample_width != 2:
            raise RuntimeError("normalized channel WAV must be 16 kHz mono signed 16-bit PCM")

        chunk_frames = max(1, int(sample_rate * (chunk_ms / 1000)))
        sent_frames = 0
        websocket_url = f"{agent_ws_url.rstrip('/')}/ws/transcribe/{session_id}"

        async with websockets.connect(
            websocket_url,
            additional_headers={"x-correlation-id": f"channel-ceiling-{session_id}"},
        ) as websocket:
            # Each chunk is what the browser would have sent during replay.
            while sent_frames < total_frames:
                frames_to_read = min(chunk_frames, total_frames - sent_frames)
                audio_chunk = wav_file.readframes(frames_to_read)

                # Empty audio means the WAV ended before the expected frame count.
                if audio_chunk == b"":
                    break

                await websocket.send(audio_chunk)
                sent_frames += frames_to_read


def fetch_session_history(
    agent_http_url: str,
    session_id: str,
    timeout_seconds: float,
) -> dict[str, object]:
    """Fetch history after a channel WebSocket closes and finalizes.

    Args:
        agent_http_url: FastAPI base URL; empty means no history endpoint can be called.
        session_id: Eval UUID whose in-memory transcript should be available.
        timeout_seconds: Seconds to wait for finalize; zero means one immediate attempt.

    Returns:
        History payload shaped like `/session/{id}/history`.

    Raises:
        httpx.HTTPStatusError: When history never becomes available.
    """
    endpoint = f"{agent_http_url.rstrip('/')}/session/{session_id}/history"
    deadline = time.monotonic() + max(0.0, timeout_seconds)

    # History can appear a moment after the browser-style WebSocket disconnect finalizes.
    while True:
        response = httpx.get(endpoint, timeout=10.0)

        # A finalized channel history is ready to combine with the other role.
        if response.status_code == 200:
            return response.json()

        # The timeout means the user-facing history never appeared for this eval session.
        if time.monotonic() >= deadline:
            response.raise_for_status()

        time.sleep(1.0)


def channel_session_id(fixture_name: str, role: str, run_key: str = "") -> str:
    """Return a valid deterministic session ID for one eval channel.

    Args:
        fixture_name: Fixture stem shown in eval reports; empty still creates a UUID.
        role: Channel role being transcribed; empty still creates a UUID.
        run_key: Run-specific salt; empty means repeated evals reuse the same session ID.

    Returns:
        UUID string accepted by `/transcribe/file` validation.
    """
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"ambient-scribe-channel-ceiling:{run_key}:{fixture_name}:{role}",
        )
    )


def combined_history_from_channels(
    fixture_name: str,
    channel_payloads_by_role: dict[str, dict[str, object]],
) -> dict[str, object]:
    """Build one scoreable history payload from separated role channels.

    Args:
        fixture_name: Fixture stem; empty means the history cannot be traced to a case.
        channel_payloads_by_role: Endpoint payload per role; empty means no transcript rows.

    Returns:
        History JSON shaped like `/session/{id}/history` for transcript-quality scoring.
    """
    combined_segments: list[dict[str, object]] = []

    # Each channel is already a known role, so no role inference is needed for the ceiling.
    for role, payload in channel_payloads_by_role.items():
        raw_segments = payload.get("segments", [])

        # Empty or malformed segments mean this role contributes no rows to the ceiling.
        if not isinstance(raw_segments, list):
            continue

        # Each NeMo batch segment becomes one visible transcript row with the channel role.
        for raw_segment in raw_segments:
            # Malformed endpoint rows are skipped so one bad segment does not kill the run.
            if not isinstance(raw_segment, dict):
                continue

            speaker_id = str(raw_segment.get("speaker_id", "spk"))
            combined_segments.append(
                {
                    "speaker_id": f"{role.lower()}_{speaker_id}",
                    "role": role,
                    "start": float(raw_segment.get("start", 0.0) or 0.0),
                    "end": float(raw_segment.get("end", 0.0) or 0.0),
                    "text": str(raw_segment.get("text", "")),
                }
            )

    combined_segments.sort(
        key=lambda segment: (
            float(segment["start"]),
            float(segment["end"]),
            str(segment["role"]),
        )
    )
    return {
        "session_id": f"{fixture_name}-channel-ceiling",
        "segments": combined_segments,
    }


def score_combined_history(
    history_path: Path,
    cutoff_seconds: float,
    doctor_textgrid_path: Path,
    patient_textgrid_path: Path,
) -> str:
    """Run the existing transcript-quality scorer against a channel history.

    Args:
        history_path: Combined channel history JSON; empty segments still produce a report.
        cutoff_seconds: Mixed replay duration used as the score cutoff.
        doctor_textgrid_path: Doctor ground-truth TextGrid.
        patient_textgrid_path: Patient ground-truth TextGrid.

    Returns:
        Human-readable scorer output; empty means the scorer failed before printing.
    """
    completed = subprocess.run(
        [
            sys.executable,
            str(TRANSCRIPT_QUALITY_SCRIPT),
            str(history_path),
            str(cutoff_seconds),
            str(doctor_textgrid_path),
            str(patient_textgrid_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def percent_metric(score_text: str, metric_label: str) -> float | None:
    """Read one percentage metric from transcript-quality output.

    Args:
        score_text: Scorer report text; empty means no metrics can be extracted.
        metric_label: Line label to read; empty means no line can match.

    Returns:
        Percentage value, or None when the scorer reported n/a or omitted the metric.
    """
    # Each line is inspected by label so table extraction survives extra report lines.
    for line in score_text.splitlines():
        match = PERCENT_LINE_PATTERN.match(line)

        # Non-percent lines, such as token counts, are not this metric.
        if match is None:
            continue

        # A different metric line belongs to another summary field.
        if match.group(1) != metric_label:
            continue

        return float(match.group(2))

    return None


def length_ratio_metric(score_text: str) -> float | None:
    """Read the hypothesis/reference length ratio from scorer output.

    Args:
        score_text: Scorer report text; empty means no ratio can be extracted.

    Returns:
        Ratio value, or None when the scorer did not print the line.
    """
    # Each report line may carry a different metric format.
    for line in score_text.splitlines():
        # Only the length-ratio line has the exact prefix used for the M17 table.
        if line.startswith("length ratio hyp/ref: "):
            return float(line.rsplit(" ", 1)[-1])

    return None


def run_fixture(
    source: FixtureChannelSource,
    run_dir: Path,
    agent_http_url: str,
    agent_ws_url: str,
    chunk_ms: int,
    timeout_seconds: float,
) -> ChannelCeilingResult:
    """Run separated-channel transcription and scoring for one fixture.

    Args:
        source: Manifest-backed fixture source; missing channels fail before scoring.
        run_dir: Root artifact directory for this eval run.
        agent_http_url: FastAPI base URL for fetching finalized history.
        agent_ws_url: FastAPI WebSocket URL for browser-like streaming.
        chunk_ms: Browser-like PCM chunk duration.
        timeout_seconds: History finalization timeout for each channel stream.

    Returns:
        Score summary for the fixture.
    """
    fixture_dir = run_dir / source.fixture_name
    raw_dir = fixture_dir / "raw"
    normalized_dir = fixture_dir / "normalized"
    channel_payloads_by_role: dict[str, dict[str, object]] = {}

    # Each role channel is transcribed independently to avoid mixed-mono cross-talk.
    for role, source_url in source.source_urls_by_role.items():
        raw_channel_path = raw_dir / f"{role.lower()}.wav"
        normalized_channel_path = normalized_dir / f"{role.lower()}.wav"
        download_channel(source_url, raw_channel_path)
        normalize_channel_wav(raw_channel_path, normalized_channel_path)
        channel_payloads_by_role[role] = transcribe_channel(
            normalized_channel_path,
            channel_session_id(source.fixture_name, role, run_dir.name),
            agent_http_url,
            agent_ws_url,
            chunk_ms,
            timeout_seconds,
        )

    history = combined_history_from_channels(source.fixture_name, channel_payloads_by_role)
    history_path = fixture_dir / "history.channel-ceiling.json"
    history_path.write_text(json.dumps(history, indent=2), encoding="utf-8")

    cutoff_seconds = fixture_cutoff_seconds(source.mixed_wav_path)
    doctor_textgrid_path = source.mixed_wav_path.with_suffix(".doctor.TextGrid")
    patient_textgrid_path = source.mixed_wav_path.with_suffix(".patient.TextGrid")
    score_text = score_combined_history(
        history_path,
        cutoff_seconds,
        doctor_textgrid_path,
        patient_textgrid_path,
    )
    score_path = fixture_dir / "transcript-quality.channel-ceiling.txt"
    score_path.write_text(score_text, encoding="utf-8")

    return ChannelCeilingResult(
        fixture_name=source.fixture_name,
        cutoff_seconds=cutoff_seconds,
        history_path=history_path,
        score_path=score_path,
        word_error_rate_percent=percent_metric(score_text, "word error rate"),
        clean_word_error_rate_percent=percent_metric(
            score_text,
            "word error rate (non-overlap)",
        ),
        overlap_word_error_rate_percent=percent_metric(
            score_text,
            "word error rate (overlap)",
        ),
        recall_percent=percent_metric(score_text, "reference vocabulary recall"),
        length_ratio=length_ratio_metric(score_text),
    )


def print_results(results: list[ChannelCeilingResult], run_dir: Path) -> None:
    """Print a compact channel-ceiling table for plan updates.

    Args:
        results: Fixture summaries; empty means no fixtures were scored.
        run_dir: Artifact directory where detailed histories and scores were written.
    """
    print("fixture                                      cutoff  wer   clean overlap recall ratio")
    # Each result row is a fixture the M17 plan can compare against mixed-mono numbers.
    for result in results:
        print(
            f"{result.fixture_name[:44]:44} "
            f"{result.cutoff_seconds:6.1f} "
            f"{format_optional_number(result.word_error_rate_percent):>5} "
            f"{format_optional_number(result.clean_word_error_rate_percent):>5} "
            f"{format_optional_number(result.overlap_word_error_rate_percent):>7} "
            f"{format_optional_number(result.recall_percent):>6} "
            f"{format_optional_number(result.length_ratio, decimals=2):>5}"
        )

    print(f"run artifacts: {run_dir.relative_to(REPO_ROOT)}")


def format_optional_number(value: float | None, *, decimals: int = 1) -> str:
    """Format optional numeric metrics for the compact report.

    Args:
        value: Metric value; None means the scorer reported `n/a`.
        decimals: Decimal places for visible table cells.

    Returns:
        Formatted number, or `n/a` when no metric exists.
    """
    # Missing metrics stay visible in the table without pretending to be zero.
    if value is None:
        return "n/a"

    return f"{value:.{decimals}f}"


def default_run_dir() -> Path:
    """Return the default artifact directory for this eval run.

    Returns:
        Timestamped path under `var/quality/channel-ceiling`.
    """
    return QUALITY_ROOT / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def main() -> int:
    """Run the channel-ceiling evaluator.

    Returns:
        Process exit code; zero means every selected fixture was scored.

    Raises:
        SystemExit: When fixture selection is empty or an unstable full run is not explicit.
    """
    args = parse_args()

    # Named fixtures are safe for targeted diagnosis; an accidental full run can destabilize NeMo.
    if not args.all and not args.fixtures:
        raise SystemExit("error: pass fixture queries, or --all with --allow-unstable-full-run")

    # The current NeMo stack crashed during M17 when all separated channels were streamed.
    if args.all and not args.allow_unstable_full_run:
        raise SystemExit("error: --all is disabled; use --allow-unstable-full-run to reproduce M17 crash evidence")

    run_dir = args.run_dir or default_run_dir()
    sources = load_fixture_sources()
    selected_sources = select_fixture_sources(sources, args.fixtures, args.all)
    results: list[ChannelCeilingResult] = []

    # Each selected fixture is scored independently so artifacts are easy to inspect.
    for source in selected_sources:
        print(f"channel-ceiling fixture={source.fixture_name}", file=sys.stderr)
        results.append(
            run_fixture(
                source,
                run_dir,
                args.agent_http_url,
                args.agent_ws_url,
                args.chunk_ms,
                args.timeout,
            )
        )

    print_results(results, run_dir)
    return 0


# Script execution runs the eval; imports only expose testable helper functions.
if __name__ == "__main__":
    raise SystemExit(main())
