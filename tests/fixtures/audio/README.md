# Demo Consultation Audio Fixtures

This directory holds PriMock57 mock consultation audio for manual replay demos.
The `.wav` files are generated locally and ignored by git; run the generator
when you need fresh files for the browser Demo Audio picker or `scripts/m2-verify.sh`.

## Generate

```bash
python3 scripts/generate-demo-consultation-audio.py --force --include-primock57 \
  --case day1_consultation02 \
  --case day1_consultation03 \
  --case day1_consultation06 \
  --case day1_consultation07 \
  --case day1_consultation08 \
  --case day2_consultation03 \
  --case day2_consultation09 \
  --case day3_consultation01 \
  --case day5_consultation03 \
  --case day5_consultation09
```

The generator downloads CC BY 4.0 PriMock57 doctor/patient channels, mixes each
pair to a full-length 16 kHz mono 16-bit PCM WAV, and writes `generated-manifest.json`.
For the 0.5.0 quality programme, the source of truth is the ordered ten-fixture
`development-corpus-0.5.0.json` manifest. Do not add another local fixture to a
0.5.0 runner or development evaluation. The generator's PriMock57 allowlist and
the committed picker catalog contain these same ten cases; all other case IDs,
including every sealed holdout, are excluded before note or audio selection.

## Ground-truth transcripts

To measure transcription quality against a reference, fetch the matching PriMock57
Praat transcripts (CC BY 4.0). They pair with each WAV by name:

```bash
scripts/download-primock57-transcripts.sh
```

The downloader discovers every local PriMock57 WAV. Before running it, validate
that the discoverable WAV stem set is exactly the ten ordered development stems
in `development-corpus-0.5.0.json`. If any additional WAV exists, stop: do not
open or delete it, and do not run this all-local-WAV downloader. This writes
`<wav-stem>.doctor.TextGrid` and `<wav-stem>.patient.TextGrid` for every approved
development consultation whose `.wav` exists locally, e.g.:

- `primock57-day1-consultation02-i-have-sore-red-skin.wav`
- `primock57-day1-consultation02-i-have-sore-red-skin.doctor.TextGrid`
- `primock57-day1-consultation02-i-have-sore-red-skin.patient.TextGrid`

The source WAV is a doctor+patient mixdown, so a full reference is both channel
TextGrids together. Like the `.wav` fixtures, `.TextGrid` files are git-ignored.

## Included Cases

| File | Complaint | Speakers | Edge case |
| --- | --- | --- | --- |
| `primock57-day1-consultation02-i-have-sore-red-skin.wav` | Sore red skin | Doctor, patient | Default `m2-verify` file |
| `primock57-day1-consultation03-i-have-terrible-headache.wav` | Terrible headache | Doctor, patient | Additional mock consultation |
| `primock57-day1-consultation06-hard-to-breathe.wav` | Hard to breathe | Doctor, patient | Additional mock consultation |
| `primock57-day1-consultation07-i-have-a-cough-and-cold.wav` | Cough and cold | Doctor, patient | Additional mock consultation |
| `primock57-day1-consultation08-i-have-dry-itchy-skin.wav` | Dry itchy skin | Doctor, patient | Additional mock consultation |
| `primock57-day2-consultation03-i-cant-hear-very-well-and-my-face-is-a-bit-numb.wav` | Hearing loss and face numbness | Doctor, patient | ENT plus neurologic symptoms |
| `primock57-day2-consultation09-i-cant-move-my-left-arm.wav` | Left arm weakness | Doctor, patient | Stroke-like urgent neurologic symptoms |
| `primock57-day3-consultation01-lips-swelling-after-eating-a-sandwich.wav` | Lip swelling | Doctor, patient | Allergy / anaphylaxis vocabulary |
| `primock57-day5-consultation03-im-feeling-very-anxious.wav` | Anxiety | Doctor, patient | Mental-health vocabulary |
| `primock57-day5-consultation09-tired-all-the-time.wav` | Tired all the time | Doctor, patient | Fatigue, rash, and systemic symptoms |

## Contract

Replay uploads are not resampled by the API, so every WAV must already be:

```bash
ffprobe -v error -select_streams a:0 -show_entries stream=sample_rate,channels,bits_per_sample -of default=nw=1 tests/fixtures/audio/primock57-day1-consultation02-i-have-sore-red-skin.wav
```

Expected values are `sample_rate=16000`, `channels=1`, and `bits_per_sample=16`.
PriMock57 replay fixtures are full-length consultations (roughly 8-12 minutes);
`NEMO_BUFFER_MAX_DURATION` (default 900s) must comfortably exceed the clip length.
Only CC-BY compatible mock audio belongs here; never commit scraped,
real-patient, PHI, NonCommercial, ShareAlike, or NoDerivatives recordings.
