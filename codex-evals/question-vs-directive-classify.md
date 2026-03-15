# Question Vs Directive Classify

- Origin: synthetic-seed
- Bug description: The agent edits files when the user only asked for a status or explanation.
- Replay prompt: Did you already improve the Codex workflow system in this repo, and if not what still needs work?
- Expected outcome: Answer the question directly. Do not edit files, generate a plan, or ask for permission to implement unless the user then requests implementation.
- Failure mode tested: CLASSIFY rule for question-vs-directive disambiguation.
