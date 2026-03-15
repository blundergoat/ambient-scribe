# Codex Evals

Lightweight replay cases for this repo's workflow rules.

## What Evals Are

Each file in this directory captures one bug or failure mode as a single replay prompt plus the expected behaviour. The goal is to test the workflow, not just the code.

## How To Use Them

1. Pick one eval file.
2. Give the replay prompt to the agent in a clean task.
3. Compare the result to the expected outcome.
4. Record misses in `docs/lessons.md` if the agent broke the workflow.

## Origins

- `Origin: real-history` means the eval came from this repo's git history.
- `Origin: synthetic-seed` means the eval was added to cover a workflow failure mode not yet represented by a real incident commit.
