# Demo Consultation Corpus

Ambient Scribe uses PriMock57 mock consultation audio for manual demos. The files
are generated locally from CC BY 4.0 source channels so the browser Demo Audio
picker has realistic medical replay material without using real patient data.

## Generate Audio

```bash
python3 scripts/generate-demo-consultation-audio.py --force --include-primock57 \
  --case day1_consultation02 \
  --case day1_consultation03 \
  --case day1_consultation05 \
  --case day1_consultation06 \
  --case day1_consultation07 \
  --case day1_consultation08 \
  --case day2_consultation02 \
  --case day2_consultation03 \
  --case day2_consultation07 \
  --case day2_consultation09 \
  --case day3_consultation01 \
  --case day3_consultation03 \
  --case day5_consultation03 \
  --case day5_consultation04 \
  --case day5_consultation08 \
  --case day5_consultation09
```

This writes ignored WAV files under `tests/fixtures/audio/`. PriMock57 files are
generated at full consultation length; `NEMO_BUFFER_MAX_DURATION` (default
900 seconds) bounds replay GPU memory. The current demo set includes 16 cases;
`PRIMOCK57_EXCLUDED_CASE_IDS` in the generator is the exclusion source of truth,
and `scripts/m2-verify.sh` defaults to case 02.

## Add A Consultation

1. Add or select a PriMock57 case in `scripts/generate-demo-consultation-audio.py`.
2. Use Apache-compatible CC-BY source material with attribution.
3. Regenerate audio and confirm the WAV contract:

```bash
ffprobe -v error -select_streams a:0 -show_entries stream=sample_rate,channels,bits_per_sample -of default=nw=1 tests/fixtures/audio/primock57-day1-consultation02-i-have-sore-red-skin.wav
```

4. Replay it through the browser Demo Audio picker on a GPU host and confirm segments,
   DOCTOR/PATIENT roles, and SOAP summary.

Generated audio must remain 16 kHz mono 16-bit PCM WAV. Never commit PHI,
real-patient audio, scraped media, or NonCommercial/ShareAlike/NoDerivatives
recordings to this Apache-2.0 repository.
