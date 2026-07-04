# Demo Audio Attribution

The generated WAV files are synthetic demo consultations created from text
stored in `scripts/generate-demo-consultation-audio.py`.

- Source text: project-authored synthetic medical consultation scripts.
- Audio engine: local FFmpeg `flite` source using bundled Flite voices.
- Patient status: no real patient, no PHI, no scraped media.
- Repository policy: generated `.wav` files are ignored by git; if a release
  intentionally bundles them, force-add only after reviewing this attribution.

Do not commit CC BY-NC, CC BY-SA, CC BY-ND, gated, scraped, or real-patient
audio to this Apache-2.0 repository.
