# strands_agents GEMINI

Focus: NeMo pipeline, session lifecycle, and role inference.

- **Ask First:** Changes to GPU capacity, NeMo workers, or audio buffer formats.
- **Footguns:** #2 (lifecycle split), #5 (singleton), #6 (grace window), #7 (DynamoDB mismatch), #9 (Ollama tool calling).
- **Check:** `strands_agents/nemo_session.py` for PCM/WebM format.
- **Run:** `pytest tests/python -q` after changes.
