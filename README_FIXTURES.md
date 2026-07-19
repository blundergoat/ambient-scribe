# Audio Fixture Setup

This project uses local WAV fixtures for the dev Demo Audio picker and
`scripts/m2-verify.sh`. The audio files are written to `tests/fixtures/audio/`
and are ignored by git.

## Prerequisites

- Python 3
- FFmpeg on `PATH`
- FFmpeg filters `flite` and `amix`
- Network access when downloading PriMock57 fixtures

Check FFmpeg support:

```bash
command -v ffmpeg
ffmpeg -hide_banner -filters | rg "flite|amix"
```

## Generate Synthetic Fixtures

Use this when you only need the project-authored demo consultations:

```bash
python3 scripts/generate-demo-consultation-audio.py --force
```

This creates the synthetic WAVs and writes:

```text
tests/fixtures/audio/generated-manifest.json
```

## Download PriMock57 Fixtures

Use this to generate the synthetic fixtures and download PriMock57 mock
consultations (default: the first three discovered cases):

```bash
python3 scripts/generate-demo-consultation-audio.py --force --include-primock57
```

Discovery is limited to the ten development-corpus consultations (day1:
02, 03, 06, 07, 08; day2: 03, 09; day3: 01; day5: 03, 09); sealed holdout
cases are never listed or downloaded. See
[docs/demo-consultation-corpus.md](docs/demo-consultation-corpus.md) for the
corpus rationale and the exact generation commands used for evaluation.

PriMock57 stores doctor and patient channels separately. The generator downloads
both channels, mixes the full consultation into one mono WAV per consultation,
and adds attribution metadata to `generated-manifest.json`.

Download one PriMock57 consultation:

```bash
python3 scripts/generate-demo-consultation-audio.py \
  --force \
  --include-primock57 \
  --case day1_consultation02
```

The output file for that example is:

```text
tests/fixtures/audio/primock57-day1-consultation02-i-have-sore-red-skin.wav
```

Download every development-corpus consultation (`--primock57-limit 0` removes
the cap):

```bash
python3 scripts/generate-demo-consultation-audio.py \
  --force \
  --include-primock57 \
  --primock57-limit 0
```

The full development-corpus download is larger than the default sample. Do not
commit the generated WAV files. PriMock57 clips are generated at full
consultation length; `NEMO_BUFFER_MAX_DURATION` (default 900 seconds) bounds
replay GPU memory.

## Verify A Fixture

Generated WAVs must be 16 kHz mono 16-bit PCM. The browser Demo Audio replay
resamples and downmixes during decode, but the batch endpoint
(`POST /transcribe/file`, used by `scripts/m2-verify.sh`) expects 16 kHz mono
input as-is:

```bash
ffprobe -v error \
  -select_streams a:0 \
  -show_entries stream=sample_rate,channels,bits_per_sample \
  -of default=nw=1 \
  tests/fixtures/audio/primock57-day1-consultation02-i-have-sore-red-skin.wav
```

Expected output:

```text
sample_rate=16000
channels=1
bits_per_sample=16
```

## Use In The App

After generating fixtures, start the app in dev mode and open the Scribe page.
The Demo Audio picker reads `tests/fixtures/audio/generated-manifest.json` and
only shows rows for WAV files that exist locally.

## Reference Transcripts

`scripts/download-primock57-transcripts.sh` fetches the matching PriMock57
ground-truth transcripts (Praat TextGrid) for WAVs that already exist locally,
naming them `<wav-stem>.doctor.TextGrid` and `<wav-stem>.patient.TextGrid`.
Quality scripts score transcription accuracy against them. Like the WAVs,
TextGrids are gitignored.

## Licensing

Synthetic fixtures are project-authored and contain no real patient data.
PriMock57 fixtures (audio and TextGrid transcripts) are CC BY 4.0 mock primary
care consultations. Never commit PHI, real-patient audio, scraped media,
NonCommercial, ShareAlike, or NoDerivatives recordings to this repository.
