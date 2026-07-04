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
  --case day1_consultation08
```

The generator downloads CC BY 4.0 PriMock57 doctor/patient channels, mixes each
pair to a 90-second 16 kHz mono 16-bit PCM WAV, and writes `generated-manifest.json`.
Cases 01, 09, and 10 are intentionally excluded from the local demo picker.

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

## Contract

Replay uploads are not resampled by the API, so every WAV must already be:

```bash
ffprobe -v error -select_streams a:0 -show_entries stream=sample_rate,channels,bits_per_sample -of default=nw=1 tests/fixtures/audio/primock57-day1-consultation02-i-have-sore-red-skin.wav
```

Expected values are `sample_rate=16000`, `channels=1`, and `bits_per_sample=16`.
Expected duration for PriMock57 replay fixtures is about 90 seconds so the Demo
Audio picker does not ask NeMo to process a full consultation in one request.
Only CC-BY compatible mock audio belongs here; never commit scraped,
real-patient, PHI, NonCommercial, ShareAlike, or NoDerivatives recordings.
