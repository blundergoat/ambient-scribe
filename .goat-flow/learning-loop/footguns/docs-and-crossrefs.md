---
category: docs-and-crossrefs
last_reviewed: 2026-07-05
---

# Docs and Cross-Reference Footguns

## Footgun: Empty bucket files are not durable cross-reference repairs

**Status:** active | **Created:** 2026-07-05 | **Evidence:** OBSERVED

**Symptoms:** Adding a bare learning-loop bucket file can make a path exist temporarily, but `goat-flow index` still has no entry to expose as durable retrieval evidence.

- **Files:** `node_modules/@blundergoat/goat-flow/dist/cli/learning-loop-index/parse-bucket.js` (search: "splitEntrySections(body, HEADING_KIND[bucket])")
- **Files:** `node_modules/@blundergoat/goat-flow/dist/cli/learning-loop-index/generate.js` (search: "entryCount: entries.length")
- **What breaks:** A local-path repair that only creates an empty Markdown bucket does not produce an indexed `## Footgun:` or `## Lesson:` row. Future agents following the index still cannot retrieve evidence for the path, and follow-up cleanup can remove the stub.
- **Evidence:** The indexer parses entry headings, not bucket file existence, and reports counts from parsed entries. Repair stale learning-loop references with real entries or by removing/updating the stale markup.
