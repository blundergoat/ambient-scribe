#!/usr/bin/env python3
"""
Generate license-clean consultation WAV fixtures.

The script uses FFmpeg's local `flite` source to create acted-style demo audio
without adding a TTS dependency or committing scraped recordings. Use it when
the Demo Audio picker or `scripts/m2-verify.sh` needs realistic medical replay files.
It can also download CC BY 4.0 PriMock57 mock consultations on request.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class DemoUtterance:
    """
    One spoken turn in a synthetic consultation.

    The generator maps each visible role to a distinct local Flite voice so the
    replay audio has a chance of diarizing into useful speakers for the UI.
    """

    role: str
    text: str


@dataclass(frozen=True)
class DemoConsultation:
    """
    Metadata and spoken turns for one demo consultation.

    Use these cases when a reviewer wants repeatable replay audio covering the
    same role and edge-case behavior as the browser scenario runner.

    Attributes:
        filename: WAV name the user selects in the Demo picker.
        complaint: Clinical scenario label shown in the generated manifest.
        expected_roles: Speaker-to-role expectation reviewers compare in replay.
        edge_case: Role or speaker pattern the case is meant to exercise.
        utterances: Spoken turns rendered into the final consultation WAV.
    """

    filename: str
    complaint: str
    expected_roles: dict[str, str]
    edge_case: str
    utterances: tuple[DemoUtterance, ...]


@dataclass(frozen=True)
class Primock57Consultation:
    """
    One downloadable mock consultation from the PriMock57 corpus.

    PriMock57 stores doctor and patient audio as separate synchronized channels.
    The generator downloads both sides and mixes them into one mono replay WAV.

    Attributes:
        case_id: PriMock57 case identifier used to group doctor and patient WAVs.
        filename: Local WAV name shown in the Demo Audio picker.
        complaint: Presenting complaint from the PriMock57 clinician note.
        doctor_url: Remote doctor-channel WAV URL; empty would make download fail.
        patient_url: Remote patient-channel WAV URL; empty would make download fail.
        source_paths: Original corpus paths shown in attribution metadata.
        note_path: Original note JSON path shown in attribution metadata.
    """

    case_id: str
    filename: str
    complaint: str
    doctor_url: str
    patient_url: str
    source_paths: tuple[str, str]
    note_path: str


VOICE_BY_ROLE = {
    "DOCTOR": "awb",
    "PATIENT": "slt",
    "NURSE": "kal",
    "FAMILY_MEMBER": "rms",
}

PRIMOCK57_AUDIO_API_URL = (
    "https://api.github.com/repos/babylonhealth/primock57/contents/audio?ref=main"
)
PRIMOCK57_AUDIO_MEDIA_BASE_URL = (
    "https://media.githubusercontent.com/media/babylonhealth/primock57/main/audio"
)
PRIMOCK57_NOTES_RAW_BASE_URL = (
    "https://raw.githubusercontent.com/babylonhealth/primock57/main/notes"
)
PRIMOCK57_CASE_FILE = re.compile(r"^(day\d+_consultation\d+)_(doctor|patient)\.wav$")
PRIMOCK57_EXCLUDED_CASE_IDS = frozenset(
    {
        "day1_consultation01",
        "day1_consultation09",
        "day1_consultation10",
    }
)
DEMO_CONSULTATIONS = (
    DemoConsultation(
        filename="osce-chest-pain-short.wav",
        complaint="chest pain",
        expected_roles={"spk_0": "DOCTOR", "spk_1": "PATIENT"},
        edge_case="m2 default two-speaker consultation",
        utterances=(
            DemoUtterance(
                "DOCTOR", "Good morning, I am Doctor Chen. What brings you in today?"
            ),
            DemoUtterance(
                "PATIENT", "Hi doctor, I have chest pain that started three days ago."
            ),
            DemoUtterance(
                "DOCTOR",
                "Is the pain sharp, and does it get worse when you take a deep breath?",
            ),
            DemoUtterance(
                "PATIENT", "It is sharp, and deep breaths make my pain worse."
            ),
            DemoUtterance(
                "DOCTOR",
                "I want an ECG and troponin blood test today, and I will monitor your vitals.",
            ),
        ),
    ),
    DemoConsultation(
        filename="urti-role-flip.wav",
        complaint="upper respiratory tract infection",
        expected_roles={"spk_0": "PATIENT", "spk_1": "DOCTOR"},
        edge_case="role flip, patient speaks first",
        utterances=(
            DemoUtterance("PATIENT", "Doctor, I feel feverish and my throat hurts."),
            DemoUtterance(
                "DOCTOR",
                "Thanks for telling me. How long have the symptoms been present?",
            ),
            DemoUtterance(
                "PATIENT", "About four days, with a cough and a blocked nose."
            ),
            DemoUtterance(
                "DOCTOR",
                "Your symptoms sound viral. I recommend fluids, paracetamol, and review if breathing worsens.",
            ),
        ),
    ),
    DemoConsultation(
        filename="diabetes-medication-review.wav",
        complaint="diabetes medication review",
        expected_roles={"spk_0": "DOCTOR", "spk_1": "PATIENT"},
        edge_case="drug names and dosage vocabulary",
        utterances=(
            DemoUtterance(
                "DOCTOR",
                "Let us review your diabetes medicines and blood glucose diary.",
            ),
            DemoUtterance(
                "PATIENT", "My morning readings are still high, usually around ten."
            ),
            DemoUtterance(
                "DOCTOR",
                "Continue metformin one gram twice daily, and we may add empagliflozin if kidney tests are safe.",
            ),
            DemoUtterance(
                "PATIENT", "Will that interact with my blood pressure tablets?"
            ),
            DemoUtterance(
                "DOCTOR",
                "We will check renal function and review your lisinopril dose before changing treatment.",
            ),
        ),
    ),
    DemoConsultation(
        filename="cardiology-family-member.wav",
        complaint="cardiology results with family member",
        expected_roles={
            "spk_0": "DOCTOR",
            "spk_1": "PATIENT",
            "spk_2": "FAMILY_MEMBER",
        },
        edge_case="three speakers",
        utterances=(
            DemoUtterance(
                "DOCTOR", "Good afternoon, I have your ECG and cholesterol results."
            ),
            DemoUtterance(
                "PATIENT", "Thank you doctor. My wife is here because we were worried."
            ),
            DemoUtterance(
                "FAMILY_MEMBER",
                "I want to understand whether the chest tightness is dangerous.",
            ),
            DemoUtterance(
                "DOCTOR",
                "The ECG is reassuring, but the LDL cholesterol is elevated, so diet changes and follow-up are important.",
            ),
            DemoUtterance(
                "PATIENT", "I can start walking daily and reduce fried food."
            ),
        ),
    ),
    DemoConsultation(
        filename="fatigue-monologue.wav",
        complaint="single-speaker fatigue handover",
        expected_roles={"spk_0": "DOCTOR"},
        edge_case="single speaker monologue",
        utterances=(
            DemoUtterance(
                "DOCTOR",
                "Patient reports fatigue for one month with irregular sleep and high caffeine intake.",
            ),
            DemoUtterance(
                "DOCTOR",
                "No weight loss, fever, chest pain, or shortness of breath reported.",
            ),
            DemoUtterance(
                "DOCTOR",
                "Plan is full blood count, thyroid function test, sleep hygiene advice, and follow-up in two weeks.",
            ),
        ),
    ),
)


def parse_args() -> argparse.Namespace:
    """Read generator options from the command line.

    Returns:
        Parsed options; defaults generate all demo WAVs under `tests/fixtures/audio`.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        default="tests/fixtures/audio",
        help="Directory where generated WAVs and manifest are written.",
    )
    parser.add_argument(
        "--case",
        action="append",
        help="Generate only a named WAV file. Can be passed multiple times.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing generated WAVs.",
    )
    parser.add_argument(
        "--include-primock57",
        action="store_true",
        help=(
            "Download and mix PriMock57 CC BY 4.0 doctor/patient WAV pairs "
            "in addition to the synthetic fixtures."
        ),
    )
    parser.add_argument(
        "--primock57-limit",
        type=int,
        default=3,
        help=(
            "Maximum PriMock57 consultations to download when no --case filter "
            "selects them. Use 0 to download every discovered pair."
        ),
    )
    return parser.parse_args()


def main() -> int:
    """Generate the requested synthetic consultations for replay demos.

    Returns:
        Process exit code; non-zero means FFmpeg/Flite could not produce user-demo audio.

    Raises:
        SystemExit: When FFmpeg is missing, so the user can install it before replay setup.
    """
    args = parse_args()
    output_dir = Path(args.output_dir)
    selected_cases = set(args.case or [])
    ffmpeg_path = shutil.which("ffmpeg")

    # Without FFmpeg/Flite the reviewer cannot generate the local demo WAVs.
    if not ffmpeg_path:
        raise SystemExit("ffmpeg is required and was not found on PATH")

    if args.primock57_limit < 0:
        raise SystemExit("--primock57-limit must be 0 or greater")

    output_dir.mkdir(parents=True, exist_ok=True)
    generated_manifest: list[dict[str, object]] = []

    # Each consultation becomes one canonical replay WAV plus manifest metadata.
    for consultation in DEMO_CONSULTATIONS:
        # Case filters let a user refresh one audio file without rebuilding all demos.
        if selected_cases and consultation.filename not in selected_cases:
            continue

        output_path = output_dir / consultation.filename
        # Existing WAVs are preserved unless the user explicitly asks to refresh them.
        if output_path.exists() and not args.force:
            generated_manifest.append(manifest_entry(consultation, output_path))
            continue

        generate_consultation_wav(ffmpeg_path, consultation, output_path)
        generated_manifest.append(manifest_entry(consultation, output_path))

    if args.include_primock57:
        for consultation in discover_primock57_consultations(
            args.primock57_limit,
            selected_cases,
        ):
            output_path = output_dir / consultation.filename
            # Downloaded fixtures are also preserved unless a refresh is requested.
            if output_path.exists() and not args.force:
                generated_manifest.append(
                    primock57_manifest_entry(consultation, output_path)
                )
                continue

            generate_primock57_wav(ffmpeg_path, consultation, output_path)
            generated_manifest.append(
                primock57_manifest_entry(consultation, output_path)
            )

    manifest_path = output_dir / "generated-manifest.json"
    manifest_path.write_text(
        json.dumps(generated_manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"generated {len(generated_manifest)} consultation manifest entries")
    print(f"manifest: {manifest_path}")
    return 0


def generate_consultation_wav(
    ffmpeg_path: str,
    consultation: DemoConsultation,
    output_path: Path,
) -> None:
    """Render one synthetic consultation as 16 kHz mono PCM WAV.

    Args:
        ffmpeg_path: Local FFmpeg binary; empty would make generation impossible.
        consultation: Script and metadata for the replay file the user selects.
        output_path: Destination WAV; parent must already exist.
    """
    with tempfile.TemporaryDirectory(
        prefix="ambient-scribe-tts-"
    ) as working_audio_dir_name:
        working_audio_dir = Path(working_audio_dir_name)
        utterance_paths: list[Path] = []

        # Generate each spoken turn independently so roles can use distinct voices.
        for index, utterance in enumerate(consultation.utterances):
            utterance_path = working_audio_dir / f"utterance-{index:02d}.wav"
            synthesize_utterance(ffmpeg_path, utterance, utterance_path)
            utterance_paths.append(utterance_path)

        silence_path = working_audio_dir / "silence.wav"
        synthesize_silence(ffmpeg_path, silence_path)
        concat_wavs(ffmpeg_path, utterance_paths, silence_path, output_path)
        verify_canonical_wav(output_path)


def discover_primock57_consultations(
    limit: int,
    selected_cases: set[str],
) -> list[Primock57Consultation]:
    """Fetch PriMock57 audio metadata and return doctor/patient WAV pairs.

    Args:
        limit: Maximum pairs to return when no explicit PriMock57 case is selected.
        selected_cases: Optional `--case` filters; accepts output filenames or case ids.

    Returns:
        Downloadable consultations in deterministic repository order.

    Raises:
        RuntimeError: When the remote metadata does not look like a file list.
    """
    entries = read_json_url(PRIMOCK57_AUDIO_API_URL)
    if not isinstance(entries, list):
        raise RuntimeError("PriMock57 audio API did not return a file list")

    case_files = collect_primock57_case_files(entries)
    selected_case_files, selected_case_complaints = select_primock57_case_files(
        case_files,
        limit,
        selected_cases,
    )

    return build_primock57_consultations(
        selected_case_files,
        selected_case_complaints,
    )


def collect_primock57_case_files(entries: list[object]) -> dict[str, dict[str, str]]:
    """Group PriMock57 API rows into doctor/patient source files.

    Args:
        entries: GitHub API rows; empty means no remote audio files were found.

    Returns:
        Map of case id to role filenames; empty means no playable PriMock pairs.
    """
    case_files: dict[str, dict[str, str]] = {}
    for entry in entries:
        # Non-file API rows cannot be downloaded as doctor/patient audio.
        if not isinstance(entry, dict) or entry.get("type") != "file":
            continue

        name = entry.get("name")
        # Missing names leave the user with nothing safe to download.
        if not isinstance(name, str):
            continue

        match = PRIMOCK57_CASE_FILE.match(name)
        # Non-consultation files in the corpus should not appear in the picker.
        if not match:
            continue

        case_id, role = match.groups()
        case_files.setdefault(case_id, {})[role] = name

    return case_files


def select_primock57_case_files(
    case_files: dict[str, dict[str, str]],
    limit: int,
    selected_cases: set[str],
) -> tuple[list[tuple[str, dict[str, str]]], dict[str, str]]:
    """Select the PriMock57 pairs the user asked to generate.

    Args:
        case_files: Discovered case file map; empty means no replay rows.
        limit: Default corpus cap when no explicit case filter is provided.
        selected_cases: Case ids or filenames requested by the maintainer.

    Returns:
        Selected file pairs plus any complaints fetched for filename matching.
    """
    selected_case_files: list[tuple[str, dict[str, str]]] = []
    selected_case_complaints: dict[str, str] = {}
    for case_id in sorted(case_files):
        files = case_files[case_id]
        # Excluded or incomplete cases cannot produce the intended replay row.
        if not is_playable_primock57_case(case_id, files):
            continue

        filename = legacy_primock57_filename(case_id)
        # Filename filters with complaint slugs need note metadata before selection.
        if selected_cases:
            complaint = read_primock57_complaint(case_id)
            selected_case_complaints[case_id] = complaint
            filename = primock57_filename(case_id, complaint)

        # User-selected cases let maintainers refresh one fixture without all audio.
        if (
            selected_cases
            and case_id not in selected_cases
            and legacy_primock57_filename(case_id) not in selected_cases
            and filename not in selected_cases
        ):
            continue

        selected_case_files.append((case_id, files))

    # The default picker only needs a small, deterministic sample.
    if not selected_cases and limit > 0:
        selected_case_files = selected_case_files[:limit]

    return selected_case_files, selected_case_complaints


def is_playable_primock57_case(case_id: str, files: dict[str, str]) -> bool:
    """Return whether a PriMock57 case can be exposed in the picker.

    Args:
        case_id: PriMock57 case id; excluded ids are hidden from users.
        files: Role filename map; missing channels cannot be mixed into replay audio.

    Returns:
        True when both channels exist and the case is allowed for local demos.
    """
    # Excluded cases should not appear in the user's Demo Audio picker.
    if case_id in PRIMOCK57_EXCLUDED_CASE_IDS:
        return False

    return "doctor" in files and "patient" in files


def build_primock57_consultations(
    selected_case_files: list[tuple[str, dict[str, str]]],
    selected_case_complaints: dict[str, str],
) -> list[Primock57Consultation]:
    """Build downloadable PriMock57 consultation records for generation.

    Args:
        selected_case_files: Selected doctor/patient pairs; empty writes no PriMock rows.
        selected_case_complaints: Cached complaints used when filename filters needed notes.

    Returns:
        Consultation metadata consumed by the audio generator and manifest writer.
    """
    consultations: list[Primock57Consultation] = []
    for case_id, files in selected_case_files:
        note_path = f"notes/{case_id}.json"
        complaint = selected_case_complaints.get(case_id) or read_primock57_complaint(
            case_id
        )
        filename = primock57_filename(case_id, complaint)
        consultations.append(
            Primock57Consultation(
                case_id=case_id,
                filename=filename,
                complaint=complaint,
                doctor_url=f"{PRIMOCK57_AUDIO_MEDIA_BASE_URL}/{files['doctor']}",
                patient_url=f"{PRIMOCK57_AUDIO_MEDIA_BASE_URL}/{files['patient']}",
                source_paths=(
                    f"audio/{files['doctor']}",
                    f"audio/{files['patient']}",
                ),
                note_path=note_path,
            )
        )

    return consultations


def legacy_primock57_filename(case_id: str) -> str:
    """Return the old case-only PriMock57 filename accepted by --case.

    Args:
        case_id: PriMock57 case id; empty would create an unusable legacy name.

    Returns:
        Back-compatible WAV filename a user may still pass to `--case`.
    """
    return f"primock57-{case_id.replace('_', '-')}.wav"


def primock57_filename(case_id: str, complaint: str) -> str:
    """Build a self-documenting local WAV filename for one PriMock57 case.

    Args:
        case_id: PriMock57 case id used to keep filenames unique in the picker.
        complaint: Presenting complaint; empty falls back to the case-only name.

    Returns:
        WAV filename matching the generated fixture names shown in the picker.
    """
    complaint_slug = slug_for_filename(complaint)
    # Empty complaint text keeps the old case-only filename usable.
    if not complaint_slug:
        return legacy_primock57_filename(case_id)

    return f"{legacy_primock57_filename(case_id).removesuffix('.wav')}-{complaint_slug}.wav"


def slug_for_filename(value: str) -> str:
    """Convert user-visible complaint text into a safe filename fragment.

    Args:
        value: Complaint text; empty or punctuation-only values produce an empty slug.

    Returns:
        Lowercase filename slug; empty means callers should use a fallback.
    """
    normalized_value = value.lower().replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", normalized_value).strip("-")


def primock57_case_label(case_id: str) -> str:
    """Convert a PriMock57 case id into a human-readable label.

    Args:
        case_id: PriMock57 case id; empty or unknown shapes fall back to spaced text.

    Returns:
        Label used in the manifest display name for the Demo Audio picker.
    """
    match = re.fullmatch(r"day(\d+)_consultation(\d+)", case_id)
    # Unknown case-id shapes still need readable text in the picker.
    if not match:
        return case_id.replace("_", " ")

    day, consultation = match.groups()
    return f"day {int(day)} consultation {int(consultation)}"


def read_primock57_complaint(case_id: str) -> str:
    """Read the presenting complaint from the PriMock57 clinician note.

    Args:
        case_id: PriMock57 case id; empty or missing notes fall back to the case label.

    Returns:
        Presenting complaint for picker filenames, or a readable case label.
    """
    note = read_json_url(f"{PRIMOCK57_NOTES_RAW_BASE_URL}/{case_id}.json")
    # Missing note shape means the picker still gets a readable filename.
    if not isinstance(note, dict):
        return case_id.replace("_", " ")

    complaint = note.get("presenting_complaint")
    # Empty complaint text falls back to the case id instead of hiding the file.
    if not isinstance(complaint, str) or not complaint.strip():
        return case_id.replace("_", " ")

    return complaint.strip()


def generate_primock57_wav(
    ffmpeg_path: str,
    consultation: Primock57Consultation,
    output_path: Path,
) -> None:
    """Download one PriMock57 pair and mix it into a canonical replay WAV.

    Args:
        ffmpeg_path: Local FFmpeg binary; empty would make mixing impossible.
        consultation: PriMock57 metadata and download URLs.
        output_path: Final 16 kHz mono PCM WAV selected by the user.
    """
    with tempfile.TemporaryDirectory(
        prefix="ambient-scribe-primock57-"
    ) as working_audio_dir_name:
        working_audio_dir = Path(working_audio_dir_name)
        doctor_path = working_audio_dir / f"{consultation.case_id}_doctor.wav"
        patient_path = working_audio_dir / f"{consultation.case_id}_patient.wav"

        download_file(consultation.doctor_url, doctor_path)
        download_file(consultation.patient_url, patient_path)
        mix_wavs(
            ffmpeg_path,
            doctor_path,
            patient_path,
            output_path,
            max_duration_seconds=primock57_replay_clip_seconds(),
        )
        verify_canonical_wav(output_path)


def read_json_url(url: str) -> object:
    """Read a JSON document from a remote URL with a stable user agent.

    Args:
        url: Remote JSON URL; empty or unreachable URLs report setup failure.

    Returns:
        Parsed JSON object; empty remote JSON returns the decoded empty value.

    Raises:
        RuntimeError: When the remote URL cannot be read for fixture discovery.
    """
    request = Request(url, headers={"User-Agent": "ambient-scribe-demo-audio"})
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except URLError as exc:
        raise RuntimeError(f"could not read {url}: {exc}") from exc


def primock57_replay_clip_seconds() -> float:
    """Return the replay length used for PriMock57 demo clips.

    Returns:
        Seconds of source audio mixed for the picker; zero would create no replay.
    """
    return 90.0


def download_file(url: str, output_path: Path) -> None:
    """Download one remote audio file and reject Git LFS pointer placeholders.

    Args:
        url: Remote WAV URL; empty or unreachable URLs report setup failure.
        output_path: Local path for the downloaded WAV; parent must already exist.

    Raises:
        RuntimeError: When the download fails or returns a Git LFS pointer.
    """
    request = Request(url, headers={"User-Agent": "ambient-scribe-demo-audio"})
    try:
        with urlopen(request, timeout=120) as response:
            with output_path.open("wb") as output_file:
                shutil.copyfileobj(response, output_file)
    except URLError as exc:
        raise RuntimeError(f"could not download {url}: {exc}") from exc

    if is_git_lfs_pointer(output_path):
        raise RuntimeError(f"{url} returned a Git LFS pointer instead of audio")


def is_git_lfs_pointer(path: Path) -> bool:
    """Detect Git LFS pointer text where a WAV payload was expected.

    Args:
        path: Downloaded file path; empty files return false for user replay.

    Returns:
        True when the file is a pointer placeholder, false when it can be audio.
    """
    git_lfs_host = "git-lfs.github.com"
    with path.open("rb") as downloaded_file:
        return downloaded_file.read(64).startswith(
            f"version https://{git_lfs_host}/spec/".encode()
        )


def mix_wavs(
    ffmpeg_path: str,
    doctor_path: Path,
    patient_path: Path,
    output_path: Path,
    max_duration_seconds: float | None = None,
) -> None:
    """Mix synchronized doctor and patient WAV files into one mono replay WAV.

    Args:
        ffmpeg_path: Local FFmpeg binary; empty would make mixing impossible.
        doctor_path: Doctor-channel WAV downloaded for the selected case.
        patient_path: Patient-channel WAV downloaded for the selected case.
        output_path: Final mono WAV path shown in the Demo Audio picker.
        max_duration_seconds: Optional clip cap; null keeps the full source.
    """
    ffmpeg_args = [
        "-i",
        str(doctor_path),
        "-i",
        str(patient_path),
        "-filter_complex",
        "[0:a][1:a]amix=inputs=2:duration=longest:dropout_transition=0[a]",
        "-map",
        "[a]",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
    ]
    # PriMock demo clips are bounded so replay fits local GPU memory and tester time.
    if max_duration_seconds is not None:
        ffmpeg_args.extend(["-t", str(max_duration_seconds)])

    ffmpeg_args.append(str(output_path))
    run_ffmpeg(ffmpeg_path, ffmpeg_args)


def synthesize_utterance(
    ffmpeg_path: str,
    utterance: DemoUtterance,
    output_path: Path,
) -> None:
    """Create one role-specific spoken turn using FFmpeg's local Flite source.

    Args:
        ffmpeg_path: Local FFmpeg binary; empty would prevent audio creation.
        utterance: Role and text the synthetic speaker says to the clinician.
        output_path: Temporary WAV path for this spoken turn.
    """
    voice = VOICE_BY_ROLE[utterance.role]
    text_file = output_path.with_suffix(".txt")
    text_file.write_text(utterance.text, encoding="utf-8")
    run_ffmpeg(
        ffmpeg_path,
        [
            "-f",
            "lavfi",
            "-i",
            f"flite=textfile={text_file}:voice={voice}",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ],
    )


def synthesize_silence(ffmpeg_path: str, output_path: Path) -> None:
    """Create the short pause heard between consultation speakers.

    Args:
        ffmpeg_path: Local FFmpeg binary; empty would prevent audio creation.
        output_path: Temporary WAV path for the pause between utterances.
    """
    run_ffmpeg(
        ffmpeg_path,
        [
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:cl=mono",
            "-t",
            "0.25",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ],
    )


def concat_wavs(
    ffmpeg_path: str,
    utterance_paths: list[Path],
    silence_path: Path,
    output_path: Path,
) -> None:
    """Join spoken turns into one replay WAV for the Demo Audio picker.

    Args:
        ffmpeg_path: Local FFmpeg binary; empty would prevent audio creation.
        utterance_paths: Spoken-turn WAVs; empty would create no consultation audio.
        silence_path: Pause inserted between turns; missing makes speakers run together.
        output_path: Final 16 kHz mono PCM WAV selected by the user.
    """
    concat_file = output_path.with_suffix(".concat.txt")
    lines: list[str] = []

    # The concat list alternates speech and short silence for natural turn-taking.
    for index, utterance_path in enumerate(utterance_paths):
        lines.append(f"file '{utterance_path}'")
        # Do not add silence after the final speaker turn.
        if index < len(utterance_paths) - 1:
            lines.append(f"file '{silence_path}'")

    concat_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        run_ffmpeg(
            ffmpeg_path,
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(output_path),
            ],
        )
    finally:
        concat_file.unlink(missing_ok=True)


def verify_canonical_wav(output_path: Path) -> None:
    """Assert the generated file matches the app replay contract.

    Args:
        output_path: Generated WAV; missing or wrong format should block demo use.

    Raises:
        RuntimeError: When audio would fail the browser replay contract.
    """
    with wave.open(str(output_path), "rb") as wav_file:
        channel_count = wav_file.getnchannels()
        sample_rate = wav_file.getframerate()
        sample_width = wav_file.getsampwidth()

    # The replay endpoint does not resample, so generated audio must already match.
    if (channel_count, sample_rate, sample_width) != (1, 16000, 2):
        raise RuntimeError(
            f"{output_path} is not 16 kHz mono s16 WAV: "
            f"channels={channel_count}, rate={sample_rate}, width={sample_width}"
        )


def wav_duration_seconds(output_path: Path) -> float:
    """Measure a generated WAV duration for the Demo Audio manifest.

    Args:
        output_path: Generated WAV shown in the picker; missing files fail generation.

    Returns:
        Duration in seconds; zero means the audio would be useless for replay.
    """
    with wave.open(str(output_path), "rb") as wav_file:
        frame_count = wav_file.getnframes()
        frame_rate = wav_file.getframerate()

    # A broken frame rate means replay progress cannot be calculated.
    if frame_rate <= 0:
        return 0.0

    return round(frame_count / frame_rate, 1)


def manifest_entry(
    consultation: DemoConsultation,
    output_path: Path,
) -> dict[str, object]:
    """Build manifest metadata that explains what the user can demo.

    Args:
        consultation: Consultation script and expected roles.
        output_path: WAV path shown in the manifest; may not exist before generation.

    Returns:
        Manifest row; empty values would make the demo source hard to audit.
    """
    return {
        "filename": output_path.name,
        "complaint": consultation.complaint,
        "speakers": sorted(set(consultation.expected_roles.values())),
        "expected_roles": consultation.expected_roles,
        "edge_case": consultation.edge_case,
        "duration_seconds": wav_duration_seconds(output_path),
        "source": "Synthetic script generated in-repo with FFmpeg libflite voices",
        "license": "Project-owned synthetic text/audio; no real patient data",
    }


def primock57_manifest_entry(
    consultation: Primock57Consultation,
    output_path: Path,
) -> dict[str, object]:
    """Build manifest metadata for one downloaded PriMock57 consultation.

    Args:
        consultation: Downloaded consultation metadata and source paths.
        output_path: Final WAV path shown in the manifest and Demo Audio picker.

    Returns:
        Manifest row; empty values would make PriMock57 attribution hard to audit.
    """
    case_label = primock57_case_label(consultation.case_id)
    return {
        "filename": output_path.name,
        "display_name": f"PriMock57 {case_label}: {consultation.complaint}",
        "dataset": "PriMock57",
        "case_id": consultation.case_id,
        "complaint": consultation.complaint,
        "speakers": ["DOCTOR", "PATIENT"],
        "expected_roles": {
            "doctor_channel": "DOCTOR",
            "patient_channel": "PATIENT",
        },
        "edge_case": (
            f"First {int(primock57_replay_clip_seconds())} seconds of a PriMock57 "
            "mock primary care consultation with separate doctor/patient "
            "channels mixed to mono"
        ),
        "duration_seconds": wav_duration_seconds(output_path),
        "clip_seconds": primock57_replay_clip_seconds(),
        "source_audio": list(consultation.source_paths),
        "source_note": consultation.note_path,
        "source": (
            "PriMock57 mock primary care consultation audio and note: "
            + ", ".join((*consultation.source_paths, consultation.note_path))
        ),
        "license": (
            "CC BY 4.0; mock consultations by Babylon clinicians and employees, "
            "not real patient audio"
        ),
    }


def run_ffmpeg(ffmpeg_path: str, args: list[str]) -> None:
    """Run FFmpeg with quiet output and fail fast on audio generation errors.

    Args:
        ffmpeg_path: Local FFmpeg binary; empty would prevent audio creation.
        args: FFmpeg arguments after global quiet/overwrite flags; empty is invalid.
    """
    command = [ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error", *args]
    subprocess.run(command, check=True)


# Running as a script is how maintainers refresh demo WAVs for manual replay.
if __name__ == "__main__":
    raise SystemExit(main())
