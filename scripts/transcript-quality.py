"""Rough transcript quality check against PriMock57 TextGrid ground truth.

Usage: python3 scripts/transcript-quality.py <history.json> <cutoff_seconds> <textgrid...>

  history.json    output of GET /scribe/{session_id}/history (the stored transcript)
  cutoff_seconds  score reference intervals starting before this time (use the
                  max "end" in history.json when the session stopped early)
  textgrid...     the fixture's .doctor.TextGrid and .patient.TextGrid files

Reports duplication inside the hypothesis (repeated 4-gram shingles - the
signature of the re-transcription bug) and bag-of-words recall against the
reference intervals that start before the cutoff.

This is the M16 Phase 0 starting point (see
.goat-flow/plans/0.3.0/M16-diarization-stability-role-confidence.md). M16 adds
speaker-attribution accuracy, flip counts, and phantom-speaker counts; M17 adds
aligned WER and segmentation metrics; M18 wraps it in an unattended runner.
Reference numbers from 2026-07-05 (windowed emission, pre-M16):
consultation-02 @31s -> dup 0.0%, recall 83.9%, ratio 0.83;
consultation-03 @70s -> dup 3.0%, recall 77.7%, ratio 0.77.
"""

import json
import re
import sys


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z']+", text.lower())


def textgrid_words(path: str, cutoff: float) -> list[str]:
    content = open(path, encoding="utf-8", errors="replace").read()
    words: list[str] = []
    # Praat interval blocks: xmin, xmax, then text = "..."
    for match in re.finditer(
        r'xmin\s*=\s*([\d.]+)\s*\n\s*xmax\s*=\s*[\d.]+\s*\n\s*text\s*=\s*"(.*?)"',
        content,
        re.S,
    ):
        start, text = float(match.group(1)), match.group(2)
        if start <= cutoff:
            words.extend(tokens(text))
    return words


def shingle_duplication(words: list[str], n: int = 4) -> float:
    if len(words) < n:
        return 0.0
    shingles = [tuple(words[i : i + n]) for i in range(len(words) - n + 1)]
    return 1 - len(set(shingles)) / len(shingles)


if len(sys.argv) < 4:
    print(__doc__)
    sys.exit(1)

history = json.load(open(sys.argv[1]))
cutoff = float(sys.argv[2])
hyp_words: list[str] = []
for segment in history.get("segments", []):
    hyp_words.extend(tokens(segment.get("text", "")))

ref_words: list[str] = []
for grid in sys.argv[3:]:
    ref_words.extend(textgrid_words(grid, cutoff))

hyp_set, ref_set = set(hyp_words), set(ref_words)
recall = len(hyp_set & ref_set) / len(ref_set) if ref_set else 0.0

print(f"hypothesis words: {len(hyp_words)}  (unique {len(hyp_set)})")
print(f"reference words (to {cutoff:.0f}s): {len(ref_words)}  (unique {len(ref_set)})")
print(f"4-gram duplication in hypothesis: {shingle_duplication(hyp_words):.1%}")
print(f"reference vocabulary recall: {recall:.1%}")
print(f"length ratio hyp/ref: {len(hyp_words) / len(ref_words):.2f}" if ref_words else "")
