# Demo Consultation Audio Fixtures

This directory holds PriMock57 mock consultation audio for manual replay demos.
The `.wav` files are generated locally and ignored by git; run the generator
when you need fresh files for the browser Demo Audio picker or `scripts/m2-verify.sh`.

## Generate mono fixtures

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
The current working set is the ten PriMock57 consultations listed below.
`development-corpus-0.5.0.json` records the same ten fixtures for quality runs,
and the committed picker catalog presents them in the browser. For now, generate
and evaluate this set; additional consultation sources can be added later.

## Generate stereo fixtures

Run the mono generator above first so `generated-manifest.json` contains the
current approved filenames and source paths. Then use the synchronized PriMock57
source channels to put the doctor on the left and the patient on the right:

```bash
set -euo pipefail

audio_dir=tests/fixtures/audio
stereo_dir="$audio_dir/stereo"
source_base_url=https://media.githubusercontent.com/media/babylonhealth/primock57/main

mkdir -p "$stereo_dir"
jq -r '.[] | [.filename, .source_audio[0], .source_audio[1]] | @tsv' \
  "$audio_dir/generated-manifest.json" |
while IFS=$'\t' read -r filename doctor_source patient_source; do
  stereo_path="$stereo_dir/$filename"
  ffmpeg -y \
    -i "$source_base_url/$doctor_source" \
    -i "$source_base_url/$patient_source" \
    -filter_complex \
      '[0:a][1:a]join=inputs=2:channel_layout=stereo:map=0.0-FL|1.0-FR[a]' \
    -map '[a]' -ar 16000 -ac 2 -c:a pcm_s16le \
    "$stereo_path"
  cp "$stereo_path" "$audio_dir/$filename"
done
```

Each final copy makes that stereo file an active browser Demo Audio fixture.
Browser replay averages both channels into the 16 kHz mono PCM stream NeMo
expects. The direct `scripts/eval-fixtures.sh` and
`scripts/eval-corrected-fixtures.sh` runners bypass that browser conversion and
still require the mono files produced by the first command.

## Ground-truth transcripts

To measure transcription quality against a reference, fetch the matching PriMock57
Praat transcripts (CC BY 4.0). They pair with each WAV by name:

```bash
scripts/download-primock57-transcripts.sh
```

The downloader compares every discovered PriMock57 WAV stem with the ten selected
development stems in `development-corpus-0.5.0.json` before opening a WAV or URL.
It exits `2` on any missing or extra stem without downloading or deleting data.
After that automatic preflight, it writes
`<wav-stem>.doctor.TextGrid` and `<wav-stem>.patient.TextGrid` for every selected
development consultation whose `.wav` exists locally, e.g.:

- `primock57-day1-consultation02-i-have-sore-red-skin.wav`
- `primock57-day1-consultation02-i-have-sore-red-skin.doctor.TextGrid`
- `primock57-day1-consultation02-i-have-sore-red-skin.patient.TextGrid`

Each stereo WAV keeps the doctor source on the left and the patient source on the
right; browser replay mixes both into one PCM stream. A full reference therefore
uses both channel TextGrids together. Like the `.wav` fixtures, `.TextGrid` files
are git-ignored.

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

The active stereo browser fixtures must already be:

```bash
ffprobe -v error -select_streams a:0 -show_entries stream=sample_rate,channels,bits_per_sample -of default=nw=1 tests/fixtures/audio/primock57-day1-consultation02-i-have-sore-red-skin.wav
```

Expected values are `sample_rate=16000`, `channels=2`, and `bits_per_sample=16`.
PriMock57 replay fixtures are full-length consultations (roughly 8-12 minutes);
`NEMO_BUFFER_MAX_DURATION` (default 900s) must comfortably exceed the clip length.
Only CC-BY compatible mock audio belongs here; never commit scraped,
real-patient, PHI, NonCommercial, ShareAlike, or NoDerivatives recordings.
