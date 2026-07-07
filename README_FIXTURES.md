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

Use this to generate the synthetic fixtures and download the default three
PriMock57 mock consultations. Cases 01, 09, and 10 are intentionally skipped.

```bash
python3 scripts/generate-demo-consultation-audio.py --force --include-primock57
```

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

Download every discovered PriMock57 consultation:

```bash
python3 scripts/generate-demo-consultation-audio.py \
  --force \
  --include-primock57 \
  --primock57-limit 0
```

The full PriMock57 download is much larger than the default sample. Do not commit
the generated WAV files. PriMock57 clips are generated at full consultation
length; `NEMO_BUFFER_MAX_DURATION` (default 900 seconds) bounds replay GPU
memory.

## Verify A Fixture

Replay uploads are not resampled by the API. Each generated WAV must be 16 kHz
mono 16-bit PCM:

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

## Licensing

Synthetic fixtures are project-authored and contain no real patient data.
PriMock57 fixtures are CC BY 4.0 mock primary care consultations. Never commit
PHI, real-patient audio, scraped media, NonCommercial, ShareAlike, or
NoDerivatives recordings to this repository.
