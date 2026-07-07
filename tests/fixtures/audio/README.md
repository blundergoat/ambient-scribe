# Demo Consultation Audio Fixtures

This directory holds PriMock57 mock consultation audio for manual replay demos.
The `.wav` files are generated locally and ignored by git; run the generator
when you need fresh files for the browser Demo Audio picker or `scripts/m2-verify.sh`.

## Generate

```bash
python3 scripts/generate-demo-consultation-audio.py --force --include-primock57 \
  --case day1_consultation02 \
  --case day1_consultation03 \
  --case day1_consultation04 \
  --case day1_consultation05 \
  --case day1_consultation06 \
  --case day1_consultation07 \
  --case day1_consultation08 \
  --case day2_consultation01 \
  --case day2_consultation02 \
  --case day2_consultation03 \
  --case day2_consultation07 \
  --case day2_consultation08 \
  --case day2_consultation09 \
  --case day3_consultation01 \
  --case day3_consultation03 \
  --case day3_consultation05 \
  --case day5_consultation03 \
  --case day5_consultation04 \
  --case day5_consultation08 \
  --case day5_consultation09
```

The generator downloads CC BY 4.0 PriMock57 doctor/patient channels, mixes each
pair to a full-length 16 kHz mono 16-bit PCM WAV, and writes `generated-manifest.json`.
Cases 01, 09, and 10 are intentionally excluded from the local demo picker.

## Ground-truth transcripts

To measure transcription quality against a reference, fetch the matching PriMock57
Praat transcripts (CC BY 4.0). They pair with each WAV by name:

```bash
scripts/download-primock57-transcripts.sh
```

This writes `<wav-stem>.doctor.TextGrid` and `<wav-stem>.patient.TextGrid` for every
consultation whose `.wav` exists locally, e.g.:

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
| `primock57-day1-consultation04-i-dont-feel-well-i-have-a-cough-and-runny-nose.wav` | Cough and runny nose | Doctor, patient | Additional mock consultation |
| `primock57-day1-consultation05-lower-abdominal-pain.wav` | Lower abdominal pain | Doctor, patient | Additional mock consultation |
| `primock57-day1-consultation06-hard-to-breathe.wav` | Hard to breathe | Doctor, patient | Additional mock consultation |
| `primock57-day1-consultation07-i-have-a-cough-and-cold.wav` | Cough and cold | Doctor, patient | Additional mock consultation |
| `primock57-day1-consultation08-i-have-dry-itchy-skin.wav` | Dry itchy skin | Doctor, patient | Additional mock consultation |
| `primock57-day2-consultation01-i-dont-hear-as-well-as-i-used-to.wav` | Hearing loss | Doctor, patient | ENT and tinnitus vocabulary |
| `primock57-day2-consultation02-i-have-a-strange-swelling-on-my-elbow.wav` | Elbow swelling | Doctor, patient | Musculoskeletal swelling / bursitis |
| `primock57-day2-consultation03-i-cant-hear-very-well-and-my-face-is-a-bit-numb.wav` | Hearing loss and face numbness | Doctor, patient | ENT plus neurologic symptoms |
| `primock57-day2-consultation07-im-having-chest-discomfort.wav` | Chest discomfort | Doctor, patient | Cardiac red-flag vocabulary |
| `primock57-day2-consultation08-ive-been-feeling-very-hot-and-sweaty-for-the-past-week.wav` | Hot and sweaty | Doctor, patient | Fever, night sweats, and travel history |
| `primock57-day2-consultation09-i-cant-move-my-left-arm.wav` | Left arm weakness | Doctor, patient | Stroke-like urgent neurologic symptoms |
| `primock57-day3-consultation01-lips-swelling-after-eating-a-sandwich.wav` | Lip swelling | Doctor, patient | Allergy / anaphylaxis vocabulary |
| `primock57-day3-consultation03-i-dont-have-much-appetite-or-energy-lately.wav` | Low appetite and energy | Doctor, patient | Constitutional symptoms and weight loss |
| `primock57-day3-consultation05-ive-been-feeling-dizzy.wav` | Dizziness | Doctor, patient | Vestibular symptom vocabulary |
| `primock57-day5-consultation03-im-feeling-very-anxious.wav` | Anxiety | Doctor, patient | Mental-health vocabulary |
| `primock57-day5-consultation04-lower-stomach-pain.wav` | Lower stomach pain | Doctor, patient | Gynaecology / abdominal pain vocabulary |
| `primock57-day5-consultation08-im-wheezy.wav` | Wheeze | Doctor, patient | Asthma and wheeze vocabulary |
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
