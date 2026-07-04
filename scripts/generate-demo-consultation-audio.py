#!/usr/bin/env python3
"""
Generate license-clean synthetic consultation WAV fixtures.

The script uses FFmpeg's local `flite` source to create acted-style demo audio
without adding a TTS dependency or committing scraped recordings. Use it when
the Demo button or `scripts/m2-verify.sh` needs realistic medical replay files.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path


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


VOICE_BY_ROLE = {
    "DOCTOR": "awb",
    "PATIENT": "slt",
    "NURSE": "kal",
    "FAMILY_MEMBER": "rms",
}

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
    """Join spoken turns into one replay WAV for the Demo button.

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
        "source": "Synthetic script generated in-repo with FFmpeg libflite voices",
        "license": "Project-owned synthetic text/audio; no real patient data",
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
