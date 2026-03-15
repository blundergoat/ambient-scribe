# Debug Diagnosis Before Fix

- Origin: synthetic-seed
- Bug description: The agent starts patching speculative fixes before tracing the failing path.
- Replay prompt: The browser sometimes shows "Identifying speakers..." forever. Investigate the cause, but do not fix it yet.
- Expected outcome: Stay in Debug mode, trace the browser -> Mercure -> Python role-update path with file:line evidence, and stop with a diagnosis and next experiment instead of editing code.
- Failure mode tested: ACT rule for diagnosis-first debugging.
