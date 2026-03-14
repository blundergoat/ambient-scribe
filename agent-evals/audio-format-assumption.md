# Eval: Audio Format Assumption

## Bug Description

`AudioBuffer` in `nemo_session.py` hardcodes 16kHz 16-bit PCM format, but the browser's MediaRecorder sends WebM/Opus. Without format detection or conversion, NeMo receives garbage audio and produces nonsensical transcriptions — silently, with no error.

**Related:** footgun #6 in docs/footguns.md, commit f7ba6b3 (WebM→WAV conversion added)

## Replay Prompt

```
The transcription output is complete gibberish — random words that don't match what was said. No errors in the logs. NeMo seems to be running fine. What's going on?
```

## Expected Outcome

1. Agent enters Debug mode
2. Agent reads `nemo_session.py` (AudioBuffer format assumptions) AND `templates/scribe/index.html.twig` (MediaRecorder configuration)
3. Agent checks `docs/footguns.md` (footgun #6 is directly relevant)
4. Agent identifies the format mismatch: browser sends WebM/Opus, NeMo expects PCM
5. Agent does NOT assume the issue is in NeMo model quality or prompt tuning

## Failure Mode Tested

- **READ**: Must read both ends of the audio pipeline (browser → Python)
- **Footgun awareness**: docs/footguns.md entry #6 should be checked
- **Silent failure diagnosis**: No errors in logs; agent must reason about data format, not just error messages
