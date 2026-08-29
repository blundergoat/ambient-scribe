# Ambient Scribe - Plain-English Summary


A high-level explanation of what this project does, how a conversation becomes a
medical note, and which AI models do the work. Written for a non-technical
reader first, with enough precision that a developer can orient from it too.

Last checked: 2026-08-29 against this repo. For the full technical inventory
see [README_STACK.md](README_STACK.md); for the detailed walkthrough see
[README_HOW_IT_WORKS.md](README_HOW_IT_WORKS.md).

## What it does

Ambient Scribe listens to a medical consultation and turns it into a reviewable
clinical note - no typing during the visit.

A clinician opens a web page and presses record. While they talk with the
patient, the conversation appears on screen in near real time as transcript
cards labelled **Doctor** and **Patient**; recording can be paused mid-visit
(paused audio is dropped, never padded with silence). When they press stop,
the system re-checks the whole recording with a slower, more accurate second
pass, then drafts a concise **SOAP note** (Subjective, Objective, Assessment,
Plan - the standard structure for clinical notes) when the clinician clicks
Generate. Each section of the note cites the exact transcript lines it came
from, so the clinician can verify any statement with one click. If a voice is
labelled as the wrong person, the clinician can click the speaker label to
swap it - the fix applies to every line from that speaker, and later automatic
updates cannot undo it.

The clinician stays in charge throughout: the output is a draft for human
review, not an autonomous medical record.

## How a conversation becomes a note

1. **Open the page.** The web app creates a unique session ID that ties
   together everything that follows.
2. **Audio streams to our own server.** The browser captures the microphone
   and streams raw audio (16 kHz PCM over a WebSocket) to our Python service.
   The audio itself never leaves infrastructure we control.
3. **Two speech models listen at once (on our GPU).** One works out *who* is
   speaking at each moment (called "diarization"); the other works out *what*
   is being said (speech recognition). At this stage the voices are just
   anonymous "speaker 0" and "speaker 1".
4. **A language model names the speakers.** Off the GPU, a language model reads
   the words and decides which anonymous voice is the Doctor and which is the
   Patient - doctors ask clinical questions and use medical terms; patients
   describe symptoms. It keeps re-checking as the visit goes on, so labels can
   improve mid-conversation.
5. **The screen updates live.** Every new transcript segment and label change
   is pushed to the browser within moments (via Mercure, a real-time event
   hub), so the clinician sees the conversation appear as it happens.
6. **Stop triggers a cleanup pass.** The live models are tuned for speed; after
   stop, a more accurate speech model re-transcribes the retained audio and the
   corrected transcript is stored alongside the live one. The system also
   records exactly what the finished visit contained, so a note can never be
   built from a partial transcript - even if a screen timeout fires early.
7. **The note is drafted on demand.** When the clinician clicks Generate, a
   language model writes the SOAP summary from the corrected transcript. It
   must cite the transcript rows it used, those citations are validated
   against stored data before anything is shown, and automated checks flag
   unsupported claims, denials, examination findings, and low-confidence
   wording for review.
8. **Review and sign-off.** The draft appears beside its source transcript in
   a tabbed panel. The clinician reads it, follows citations back to the
   source lines, and fixes any wrong speaker labels. Copy buttons produce
   clean plain text that always states the note's source, what the automated
   checks flagged, and that it is not yet clinician reviewed.

## The AI models used

Four distinct models do the work - three speech models from NVIDIA that run on
our own local machine GPU, and one language model in the cloud (used for both language jobs).
The sixth row covers the one text-cleanup step that is deliberately *not* AI.

| # | Job                                            | Model | Where it runs                                      |
|---|------------------------------------------------|---|----------------------------------------------------|
| 1 | Who is speaking right now                      | NVIDIA Streaming Sortformer - `nvidia/diar_streaming_sortformer_4spk-v2.1` | local machine GPU, live streaming / while recording |
| 2 | What is being said, live streaming             | NVIDIA Multitalker Parakeet 0.6B - `nvidia/multitalker-parakeet-streaming-0.6b-v1` | local GPU, live streaming                          |
| 3 | More accurate re-transcription after stop      | NVIDIA Parakeet Unified 0.6B - `nvidia/parakeet-unified-en-0.6b` | local GPU, after stop                              |
| 4 | Decide who is Doctor vs Patient                | Anthropic Claude Haiku 4.5 - `au.anthropic.claude-haiku-4-5-20251001-v1:0` via AWS Bedrock | AWS cloud                      |
| 5 | Write the SOAP note                            | Same Claude Haiku 4.5 model as #4 | AWS cloud                      |
| 6 | Fix commonly misheard drug and condition names | Not a model - curated lookup file `strands_agents/data/medical_lexicon.txt` | local CPU, live streaming                          |

Two cost profiles: the NVIDIA speech models are open models that run on our
own hardware with no per-use fee - the two live models are baked into the
Docker image at build time, and the post-stop model is fetched once (pinned by
checksum) into a local cache. The language model is pay-per-use through AWS
Bedrock, and only receives transcript *text*, never audio.

For offline or fully-local development, jobs 4 and 5 swap to **Qwen 3.5 9B**
(`qwen3.5:9b`) running on CPU via Ollama - a one-line configuration change
(`ROLE_AGENT_MODEL_PROVIDER`), after which nothing leaves the machine.

Not everything is a model. Row 6, the **medical lexicon**
(`strands_agents/data/medical_lexicon.txt`), is a human-reviewed text file
where each line pairs a correct clinical term with the ways speech recognition
commonly mishears it - "metro pro lol" → **metoprolol**, "high per tension" →
**hypertension**. On by default (disable with `MEDICAL_BOOST_ENABLED=0`), the
transcription pipeline swaps those exact phrases for the correct spelling the
moment the speech model produces text, so the fix reaches the live transcript,
the summary, and downloads alike. It is deliberately conservative: exact
whole-word matches only, and it never guesses - anything not on the list stays
as heard. Every canonical/variant pair has its own row in a companion ledger,
`strands_agents/data/medical_lexicon_review.json`, carrying its category,
guard sentences, source evidence, review identity, and safety rationale, and
binding the exact `.txt` bytes by SHA-256 - audit documentation for reviewers
and QA scripts; the running app reads only the `.txt` file. Two other non-AI
helpers: a keyword-rule fallback that supplies low-confidence Doctor/Patient
labels if the language model fails mid-visit, and a governed project-authored
clinical knowledge file (`strands_agents/data/clinical_knowledge.json`) of
reviewed documentation-checklist cards that can ground the summary prompt
(keyword lookup, off by default, not a licensed guideline corpus).

## Why it's built this way

- **One GPU, speech only.** Live transcription is compute-hungry and
  time-critical, so the speech models own the single GPU outright. Role labels
  and summaries can tolerate a second of latency, so they run in the cloud or
  on CPU. This is a hard rule in the codebase, not a preference.
- **Audio stays home.** Speech-to-text is self-hosted, so patient audio never
  goes to a third party. The only external AI call sends transcript text to
  AWS Bedrock in the Australia region - and the Ollama mode removes even that.
- **Live and accurate are different problems.** The streaming models make words
  appear instantly; the post-stop second pass makes the note trustworthy. The
  summary is built from the corrected transcript whenever it exists and matches
  the finished visit; otherwise it falls back to the live rows and says so.
- **It degrades safely.** If the language model becomes unreachable mid-visit,
  transcription keeps flowing with a warning banner and rule-based fallback
  labels; new consultations are blocked up-front by a pre-flight model health
  check rather than starting a visit that can't produce a note.

## The moving parts (developer orientation)

| Service | What it does | Local port |
|---|---|---|
| Symfony web app (PHP 8.3, Symfony 6.4) | Serves the `/scribe` page, mints session IDs, injects config, proxies history/role/summary requests | 48082 |
| FastAPI agent (Python) | WebSocket audio ingest, NeMo speech models, transcript storage, role-inference queue, correction, summaries | 48101 |
| Mercure hub | Pushes transcript/role/summary events to the browser over server-sent events | 48137 |
| Ollama (optional Compose profile) | CPU-only local language model for offline development | in-network only |

The whole stack runs with `docker compose up --build` and requires an NVIDIA
GPU with the Container Toolkit. Transcripts live in memory by default (SQLite
optional) with a 2-hour retention window, and a finished visit's audio stays
correctable for 15 minutes; Mercure only carries events and is never the
durable store. The frontend is Twig templates with vanilla JS modules - no SPA
framework.

## How well does it work today

Measured on internal test recordings (the public PriMock57 mock-consultation
corpus plus manual runs) - internal evaluation, not clinical validation:

- The newer session-long **streaming engine** attributes **85–90%** of words to
  the correct speaker on real-time replays, versus roughly 40% strict live
  attribution for the older per-window engine on the same tests (July 2026
  session evidence) - which is why streaming is now the local development
  default.
- Successive role-labelling refinements lifted one manual evaluation run from
  89.7% to **95.6%** correct attribution, with the confident-error rate halved
  (internal run, July 2026).
- The post-stop correction pass measurably improves transcript accuracy and
  speaker attribution over the live view on every fixture tested.
- A stricter regime now replaces these ad-hoc numbers. Release 0.5.0 froze a
  ten-consultation baseline with acceptance limits ratified before any replay,
  three full runs per lane, and independent transcript and note gates, so
  quality claims from here are judged against rules chosen in advance.

## Where it stands

Working end-to-end demonstrator in active development (latest release 0.5.1,
2026-08-01). Honest gaps: the local page has no login layer yet, production infrastructure exists
as Terraform scaffolding rather than a hardened deployment, and the clinical
knowledge file is reviewed but synthetic proof-of-concept data that stays
inactive by default. Quality is tracked continuously through a scripted
fixture-evaluation pipeline rather than ad-hoc testing.

## Read more

- [README.md](README.md) - quick start and architecture sketch
- [README_HOW_IT_WORKS.md](README_HOW_IT_WORKS.md) - detailed system walkthrough
- [README_STACK.md](README_STACK.md) - full model, service, and dependency inventory
- [README_CLINICAL_INTELLIGENCE.md](README_CLINICAL_INTELLIGENCE.md) - medical normalisation and summary grounding
- [CHANGELOG.md](CHANGELOG.md) - feature-by-feature history with evidence
