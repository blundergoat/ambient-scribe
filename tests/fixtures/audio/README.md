# Demo Consultation Audio Fixtures

This directory holds synthetic consultation audio for manual replay demos.
The `.wav` files are generated locally and ignored by git; run the generator
when you need fresh files for the browser Demo button or `scripts/m2-verify.sh`.

## Generate

```bash
python3 scripts/generate-demo-consultation-audio.py --force
```

The generator uses local FFmpeg with the `flite` source, creates 16 kHz mono
16-bit PCM WAV files, and writes `generated-manifest.json`.

## Included Cases

| File | Complaint | Speakers | Edge case |
| --- | --- | --- | --- |
| `osce-chest-pain-short.wav` | Chest pain | Doctor, patient | Default `m2-verify` file |
| `urti-role-flip.wav` | URTI | Patient, doctor | Patient speaks first |
| `diabetes-medication-review.wav` | Diabetes review | Doctor, patient | Drug names and dosage |
| `cardiology-family-member.wav` | Cardiology results | Doctor, patient, family member | Three speakers |
| `fatigue-monologue.wav` | Fatigue handover | Doctor | Single speaker |

## Contract

Replay uploads are not resampled by the API, so every WAV must already be:

```bash
ffprobe -v error -select_streams a:0 -show_entries stream=sample_rate,channels,bits_per_sample -of default=nw=1 tests/fixtures/audio/osce-chest-pain-short.wav
```

Expected values are `sample_rate=16000`, `channels=1`, and `bits_per_sample=16`.
Only synthetic or CC-BY compatible audio belongs here; never commit scraped,
real-patient, PHI, NonCommercial, ShareAlike, or NoDerivatives recordings.
