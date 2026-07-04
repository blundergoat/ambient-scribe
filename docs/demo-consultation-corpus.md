# Demo Consultation Corpus

Ambient Scribe uses synthetic consultation audio for manual demos. The files
are generated locally so the browser Demo button has realistic medical replay
material without committing scraped audio or real patient data.

## Generate Audio

```bash
python3 scripts/generate-demo-consultation-audio.py --force
```

This writes ignored WAV files under `tests/fixtures/audio/`, including the
default `osce-chest-pain-short.wav` expected by `scripts/m2-verify.sh`.

## Add A Consultation

1. Add a `DemoConsultation` entry in `scripts/generate-demo-consultation-audio.py`.
2. Use synthetic text or Apache-compatible CC-BY source material with attribution.
3. Regenerate audio and confirm the WAV contract:

```bash
ffprobe -v error -select_streams a:0 -show_entries stream=sample_rate,channels,bits_per_sample -of default=nw=1 tests/fixtures/audio/osce-chest-pain-short.wav
```

4. Replay it through the browser Demo button on a GPU host and confirm segments,
   DOCTOR/PATIENT roles, and SOAP summary.

Generated audio must remain 16 kHz mono 16-bit PCM WAV. Never commit PHI,
real-patient audio, scraped media, or NonCommercial/ShareAlike/NoDerivatives
recordings to this Apache-2.0 repository.
