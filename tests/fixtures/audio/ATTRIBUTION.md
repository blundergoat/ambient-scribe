# Demo Audio Attribution

The generated WAV files are PriMock57 mock primary-care consultations mixed from
separate doctor and patient channels by `scripts/generate-demo-consultation-audio.py`.

- Source audio: Babylon Health PriMock57 `audio/*_doctor.wav` and `audio/*_patient.wav`.
- License: CC BY 4.0.
- Patient status: mock consultations by clinicians and employees, no real patient audio.
- Processing: local FFmpeg mixdown to 16 kHz mono 16-bit PCM WAV.
- Repository policy: generated `.wav` files are ignored by git; if a release
  intentionally bundles them, force-add only after reviewing this attribution.

Ground-truth transcripts (`*.doctor.TextGrid`, `*.patient.TextGrid`):

- Source: PriMock57 `transcripts/dayN_consultationNN_{doctor,patient}.TextGrid`.
- License: CC BY 4.0 (same corpus as the audio).
- Purpose: per-channel Praat reference transcripts for measuring transcription quality.
- Fetch: `scripts/download-primock57-transcripts.sh` (named after the paired WAV).
- Repository policy: `.TextGrid` files are ignored by git like the `.wav` fixtures.

Do not commit CC BY-NC, CC BY-SA, CC BY-ND, gated, scraped, or real-patient
audio to this Apache-2.0 repository.
