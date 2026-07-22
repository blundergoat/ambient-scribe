# Changelog

## Unreleased

- **Stopped visits can repair wrong-speaker folds behind a default-off flag** - With `NEMO_CORRECTION_REDIARIZATION=1`, the post-visit correction pass rebuilds speaker structure from the retained full audio, labels the rebuilt voices with one role-agent pass, and applies the frozen two-witness policy to each fold-suspect span: only row-level roles both witnesses agree on change, through the existing auto-row exception lane, on both the live rows and the corrected artifact. Flag-off corrections stay byte-identical, trimmed visits keep their ADR-006 bypass, audio beyond the proven capacity envelope (860s, covering the 858.2s ladder maximum) gates off, and a rebuild failure never costs the corrected transcript. The ADR-010 policy code moved to its runtime home (`strands_agents/rediar_rebuild.py`); the offline comparer CLI delegates to it.
- **Fold repairs now have a frozen two-witness decision rule** - A new offline comparer decides, per folded span, whether the corrected transcript keeps the live speaker or takes that span's true voice, requiring the rebuild's slot structure and the wording-based role label to agree before anything changes; on the frozen specimens every harmful fold improves and the clean control is untouched (proposed ADR-010, QA tooling and tests only - no runtime change yet).
- **Role labels handle split voices honestly** - The role agent's instructions now state that diarization can split one person across several speaker labels: each label is judged from its own utterances, labels may share a role, and unclear labels are omitted instead of forced into a one-doctor-one-patient split. Measured on rebuilt offline transcripts: the three-slot fixture reaches its oracle ceiling in every repetition with no change on the two-slot fixture.
- **Local health check no longer fails a healthy stack** - The roles probes now use a valid session UUID instead of the literal `test`, which the agent correctly rejects as a malformed visit; a fully healthy stack reads as healthy again and evaluation gates can trust the script's exit code.
- **Evaluation can run exactly the selected ten-case corpus** - Both fixture evaluators accept `--development-corpus`, which validates the manifest, order, and file hashes before any audio opens and refuses mixed or implicit selections, so a quality baseline can never silently score the wrong consultation set.
- **Named QA replays can show when two voices talked over each other** - Operator-enabled replays now record count-only pairwise co-activity evidence per speaker-slot pair (co-active and exclusive frames per browser window) beside the existing fold evidence; ordinary visits keep these diagnostics absent, no transcript wording is ever stored, and flag-off replays stay byte-identical (6/6 canonical transcript and continuity hashes across cold pre/post runs).

## v0.5.0 - 2026-07-20

Establishes auditable clinical quality through safer SOAP claims, traceable evidence, deterministic evaluation, and manual ASR review.

- **Unsupported completed actions now ask for review** - SOAP notes flag completed/arranged actions lacking transcript evidence; wording and workflow remain unchanged.
- **Saved-note evidence stays traceable** - Offline checks reject bad sources, validate saved-row citations, and separate unsupported claims.
- **Clinical truth kept separate** - Checks separate spoken truth, saved evidence, and valid SOAP claims without gold-transcript repair.
- **Transcript and note gates stay independent** - Independent gates stop lower word error masking unsafe claims, attribution/source errors, or actions.
- **Clinical assets checked before users see them** - A CPU-only gate blocks unapproved, unsafe, ambiguous, or reused clinical assets.
- **Unified post-visit ASR ready for manual review** - Stopped visits use exact `parakeet-unified-en-0.6b` via pinned NeMo and persistent cache, proving setup—not accuracy—without affecting live transcription.
- **Spoken instructions remain clinical text** - A fixture proves patient instructions cannot alter evaluation or consume model/corpus resources.
- **Anxiety-consult outcomes made executable** - Seven checks pin therapy/alcohol uncertainty, chest-pain/panic conflict, unsafe drug denial, supported suicidality, and blood-test status.
- **Medication/allergy scoring cases pinned** - Seven CPU-only cases test term/speaker errors, omissions, insertions, trust, and SOAP abstention.
- **High-risk note failures pinned first** - Red specimens pin anxiety, lane/overlap handling, deterministic reports, transcript instructions, and missing fixtures.
- **Transcript quality stays lane-specific** - Offline scoring separates lanes/overlap, checks terms/speakers, and exposes unrepaired assembly defects.
- **Quality acceptance limits ratified before replay** - Fixed limits cover quality, latency, GPU, review, aggregation, retries, and improvement.
- **Baseline replay rules frozen first** - Fixed visits, browser pacing, sequential GPU, health evidence, and three runs prevent replacement.
- **Quality reports repeat byte-for-byte** - Immutable evidence yields byte-identical reports without hiding errors or unsafe omissions.
- **Ten-case development corpus selected** - One ordered ten-visit manifest verifies hashes and rejects missing, extra, or reordered cases.
- **Demo picker matches evaluation exactly** - The picker mirrors the selected ten-case manifest.
- **Provider baseline deferred safely** - M01 stays provider-free until context is validated, default-off, and switchable; notes remain non-quantitative defect examples.
- **Anxiety truth fixture versioned** - Seven checks pin source/row identities, allowed states, and an unsafe note for deterministic scoring.
- **Medication and allergy regression frozen** - Three runs pin Metformin, losartan, amlodipine, and penicillin failures with lane/source metadata without implying rewrites or unseen results.
- **Cross-lane evidence registered** - Six artifacts bind the left-arm overlap; unobserved confidence, source choice, and SOAP output stay unavailable.
- **Clinical asset safety rules frozen** - A default-off contract, pair ledger, and eight red cases block unreviewed, rewritten, injected, or unapproved assets.
- **Clinical data contracts separated** - Evidence separates prompt/live/corrected/inactive-decoder lanes; 39 variants lack exact-pair approval.
- **Clinical-data debt made explicit** - Debt remains: three cards fail review, 39 variants lack approval, and legacy matching affects six of ten cases.

## v0.4.0 - 2026-07-17

Improves transcript reliability, note safety, confidence cues, long-visit handling, and local setup.

- **Incomplete-note race pinned for repair** - The case where a note was generated from an incomplete transcript and silently omitted the visit's emergency instructions is now frozen as a deterministic fixture with integrity tests, so the upcoming terminal-source gate can be built and proven against the exact failure a clinician would experience.
- **Notes only from the finished visit** - Summaries and transcript correction now bind to a terminal source attestation captured at finalization: a browser timeout can release the waiting screen but can no longer trigger a note from a partial transcript, a correction that loses any meaningful row is rejected instead of stored, over-limit visits get an explicit note-unavailable state instead of a silently shortened note, and a late role result can no longer relabel a finished draft. When finalization arrives after the wait, it unlocks note generation and prepares transcript correction but never starts a note without a clinician click.
- **Copies that paste what you see** - Copying the transcript no longer glues adjacent rows together, and new Copy transcript / Copy draft note buttons produce clean plain text built from the real note and rows: provenance counters, buttons, and developer text can never leak into pasted clinical text, review markers survive as readable text, and a note that cannot be honestly generated cannot be copied at all.
- **Three status truths in every copied note** - Copied drafts state where their text came from (corrected transcript or clearly labelled live fallback), what automated checks flagged, and that they are not clinician reviewed. Claim-level cues remain visible beside affected wording without adding a separate status list above the note.
- **Misheard medication names can no longer pose as prescriptions** - Four observed medication mishearings (Luratidine, Pyritin, Fexaphenidine, emolons) now normalize to their reviewed correct names in the live transcript, and any low-confidence misheard clinical term that reaches the note is visibly flagged for review instead of printing as a confident Plan item. Ordinary words, correct spellings, and ambiguous product mentions are never rewritten, proven against the full official consultation corpus.
- **Five misattributed transcript rows keep their true speaker** - A patient offering more detail ("do you want to know more about it?") no longer flips to Doctor, the doctor's "I've got to say..." aside no longer reads as a patient complaint, and three doctor question fragments in the corrected transcript no longer inherit the patient's label from the answer sitting next to them. Genuine complaints, short patient continuations, and manual corrections keep their existing protections.
- **More reliable correction checks** - Batch checks now continue after safe failures, report clear totals, and use a more reliable browser test server.
- **Better denial checks** - Note checks now consider the clinician's full question and the patient's full answer, reducing false warnings without accepting unsupported denials.
- **Confidence retained without transcript clutter** - Live and corrected rows keep measured confidence for downstream note checks while transcript rows remain visually plain; affected note wording is marked for review without changing its text.
- **Cross-talk behaviour reviewed** - Testing confirmed that short patient speech can be folded into the doctor stream. Alternative policies made attribution worse, so they were not shipped.
- **Duplicate speaker checks added** - Privacy-safe checks can identify one voice appearing under two speaker labels. A proposed guard made transcripts worse and was removed.
- **Fewer long transcript freezes** - An optional release limit reduces long pauses and large catch-up bursts while leaving existing behaviour as the default.
- **Broader note-quality baseline** - Full-length visits now expose unsupported claims, weak warnings, and input truncation more consistently during evaluation.
- **Overlapping speech measured** - Testing confirmed that overlapping speech still causes wording and speaker errors, so a future model change remains necessary.
- **Safer long-visit correction** - Long recordings are corrected in ordered chunks, one temporary GPU failure is retried, and fallback notes clearly identify their source.
- **Uncertainty and quotes stay faithful** - Notes describe unclear audio as a recording limitation and only quote words found in the matching speaker's transcript.
- **More precise fidelity checks** - Denial checks use nearby sentence context, ignore misleading uncertainty phrases, and keep the better draft after regeneration.
- **Complete endings in long notes** - When a visit exceeds the input limit, the note keeps the opening and closing rows and warns when middle content was omitted.
- **Confidence on every measured row** - Live and corrected transcript rows can carry a stored confidence score that follows them through the interface and summary flow.
- **Notes checked before display** - Each note is checked for unsupported certainty, denials, and examination claims. Remaining concerns are visibly marked after one retry.
- **Word confidence validated** - Both supported speech models produced useful word-level confidence without changing transcript output or destabilising the GPU.
- **Broader full-length evaluation corpus** - The accepted quality baseline covered full-length consultations with day-based labels and matching speaker references; the local demo picker used a curated subset.
- **Consistent model defaults** - Production uses Bedrock for roles and summaries in one configured AWS region. Local startup falls back to lightweight Ollama only when no provider is configured, while `.env.example` deliberately selects Bedrock.
- **Late rows stay in the right card** - Delayed wording is inserted into the correct earlier speaker card without changing spoken order or later turns.
- **Clearer WSL2 GPU failure** - Startup now detects an empty GPU response and prints the steps needed to restart WSL2 and Docker Desktop.

## v0.3.0 - 2026-07-07

Adds corrected transcripts, evidence-linked notes, stronger speaker handling, safer summaries, and
a more focused medical workflow.

- **Finished visits keep their role state** - Late role reads and corrections no longer recreate empty state or wipe the confidence badge, while manual labels still persist.
- **Notes stay faithful to the consultation** - Patient uncertainty remains uncertain, assessments use clinician-stated diagnoses, and reported symptoms stay out of examination findings.
- **Fewer false source warnings** - Source checks now recognise common doctor question introductions without changing live speaker decisions.
- **Citation links open the transcript** - Source links switch to the Transcript tab, scroll to the evidence, and briefly highlight every cited block.
- **Compact source popovers** - Each cited section has an accessible source count and popover instead of a row of always-visible citation chips.
- **Note and Transcript tabs** - The summary panel now shows the generated note and its corrected source transcript in separate keyboard-friendly tabs.
- **Safe citation diagnostics** - Invalid or missing citations are counted in logs without recording consultation wording.
- **Readable transcript stitching** - Adjacent rows from the same speaker are grouped for display while keeping their source IDs and timestamps.
- **Medical term correction enabled** - Common drug and condition corrections are on by default and can still be disabled when required.
- **No inline citation clutter** - Timestamp and row markers are removed from note prose while structured source links remain available.
- **Safer corrected-role cleanup** - Weaker wording cues are used only when the original speaker structure is clearly unreliable.
- **Real-time evaluation pacing** - Correction checks can run at real listening speed, making browser replays the trusted measure of live behaviour.
- **Word echoes split correctly** - A clinician repeating a patient's content word can be separated from the patient's answer when the timing and wording are clear.
- **Orphan speaker rows repaired** - Early rows created under temporary speaker identities can be relabelled without overriding clinician corrections.
- **Streaming enabled for local development** - Local sessions now use the session-long streaming engine by default, while automated checks keep the stable windowed engine.
- **Native word timing** - Post-visit correction now prefers the speech model's word timestamps over estimated timing.
- **Identity and echo rows separated** - Patient identity answers can be split from a clinician's repeated acknowledgement when timing evidence is strong.
- **Narrow timing changes only** - Testing supported word timing for specific echo boundaries but rejected broad transcript realignment.
- **Corrected-source checker** - A privacy-safe checker now reports corrected rows whose wording conflicts with their visible Doctor or Patient label.
- **Offline diarisation evaluation** - A repeatable comparison now measures full-recording speaker models before any runtime change is considered.
- **Post-visit timing confirmed** - Testing showed that word timestamps are safe for correction without enabling the unstable live timestamp path.
- **Corrected transcript evaluation** - Demo recordings can be replayed, corrected, scored, and compared with the live transcript in one run.
- **Correction before summary** - Stopping a visit now creates a corrected transcript before generating the note, with a safe fallback to live rows.
- **Corrected transcript access** - The exact corrected transcript used by summaries can be fetched for review and testing.
- **Evidence-linked summaries** - Notes prefer corrected rows and include validated links back to the transcript evidence.
- **Session-long streaming engine** - One speaker cache now follows the whole visit, greatly reducing mid-visit Doctor and Patient label swaps.
- **Stop waits for final words** - Ending a recording keeps the event stream open briefly so the final transcript rows reach the browser and note.
- **Late role changes recorded** - Speaker changes that arrive after finalisation are counted without altering existing quality records.
- **Per-row speaker correction** - Clinicians can correct one transcript line without relabelling every row from the same speaker.
- **Session quality records** - Completed visits record timing, transcript delivery, speaker stability, role confidence, and errors without storing clinical wording.
- **Repeatable demo evaluation** - Demo recordings can be streamed through the real path and compared with earlier quality results.
- **Speaker attribution scoring** - Evaluation now measures whether each clean transcript row is assigned to the right role.
- **Clearer role diagnostics** - Reports separate speech-model speaker mixing from role-mapping and fallback errors.
- **Honest mapping limits** - Quality reports now distinguish realistic two-role accuracy from a looser diagnostic best case.
- **Word-level quality measures** - Reports include word errors, overlap performance, fragment density, and repeated seam wording.
- **Reference transcripts added** - Demo consultations now have matching speaker-labelled reference transcripts for objective scoring.
- **Helpful summary errors** - Failed notes show practical model-recovery guidance instead of a generic error.
- **Live model warnings** - The interface warns during a visit when the role or summary model cannot be reached.
- **Model check before recording** - Recording and replay stop early with clear guidance when the required AI model is unavailable.
- **Connection status in the Dev Panel** - A simple indicator shows whether the live event stream is connected.
- **Stable speaker-confidence badge** - The badge now settles after inference and clearly distinguishes identified, uncertain, and unclear roles.
- **Log and evaluation reports** - New tools summarise process health and role quality without requiring GPU access for every check.
- **Current stack documented** - The active models, services, topics, and key dependencies are described in one place.
- **Clinical intelligence documented** - Medical term correction and summary grounding are explained with their safety limits and controls.
- **Synthetic demo consultations** - Five licence-safe recordings cover chest pain, role changes, extra speakers, drug names, and monologues.
- **Optional medical phrase correction** - A reviewed medical lexicon can correct common speech-recognition mistakes after transcription.
- **Clinical hints tested** - A review-only hints panel was introduced during development and later removed from the release.
- **Source-check reports saved** - Corrected transcript evaluations now include both readable and structured source-label results.
- **Automatic correction on Stop** - Live and replay sessions start correction and summary generation as soon as finalisation completes.
- **Mixed rows split more safely** - Clear doctor prompts and patient answers are separated while uncertain identity echoes remain together.
- **Better corrected role labels** - Strong first-person, body-location, and short-answer cues can repair obvious source-label mistakes.
- **More stable corrected alignment** - Short consumed words no longer push later corrected text into the wrong speaker rows.
- **Mixed cards ask for review** - A transcript card with conflicting row roles shows a neutral review label instead of a confident speaker label.
- **Clinical hints removed** - The hints panel, event feed, and summary payload were removed; medical summary grounding remains.
- **Offline diarisation not promoted** - Full-recording speaker models did not meet the role-attribution bar, so runtime behaviour stayed unchanged.
- **External diarisation remains gated** - The optional external model was not tested because its dependency and access requirements were not approved.
- **Full-audio Sortformer rejected** - It reduced some word errors but created too many speaker identities and sharply reduced role accuracy.
- **Broad word-timed alignment rejected** - It improved wording but moved too many rows to the wrong role, so it remains evaluation-only.
- **Seam trimming rejected** - Removing repeated seam wording also increased overall word errors, so the change was reverted.
- **Anchor-based correction alignment** - Corrected text now follows live-text anchors and keeps visible rows that the second pass misses.
- **Summary requests preserve history** - Browser rows are merged into stored transcript history instead of replacing rows the browser did not receive.
- **Clear finalisation logs** - Window logs now distinguish normal chunks from the final transcript flush.
- **Held-tail speaker anchor rejected** - Reusing earlier held rows did not improve speaker accuracy, so the experiment was removed.
- **Less biased role inference** - Automatic mappings are no longer fed back into the role prompt, reducing the chance that an early mistake reinforces itself.
- **Automatic row exceptions** - Clear wording cues can relabel or mark individual rows uncertain without touching clinician corrections.
- **Stricter trend reports** - Evaluation tables lead with strict role accuracy, uncertainty, confident errors, and realistic mapping limits.
- **Row-based summary input** - Summaries receive each corrected transcript row rather than a merged card that can hide role differences.
- **Honest role badge** - Green confidence now requires both a confident mapping and stable speaker identities; unstable sessions ask for label review.
- **Stable evaluation history** - Reports wait for late role decisions before scoring the labels a clinician would actually see.
- **Strict Doctor and Patient metrics** - Unknown rows remain in the accuracy denominator so uncertainty cannot inflate the headline score.
- **Speaker continuity diagnostics** - Privacy-safe window records show how speaker identities were matched, merged, or remapped.
- **Row-level attribution diagnostics** - Each scored row can be traced to its expected role, visible role, overlap state, and speaker window.
- **Safer medical corrections** - Phrase fixes preserve capitalisation, tolerate missing data, and disable risky replacements until reviewed.
- **Medical correction coverage** - Quality checks fail when an active correction lacks reviewer context or drifts from the approved list.
- **Reviewed PHP quality exception** - Quality tooling accepts the temporary client constraint without hiding unrelated PHP issues.
- **Fewer transcript fragments** - Adjacent word-sized pieces from the same speaker are joined before they reach the browser.
- **Cleaner punctuation spacing** - Missing spaces after sentence punctuation are repaired before transcript rows are displayed.
- **Overlap benchmark guarded** - A separated-channel evaluation was kept limited after larger runs exhausted GPU memory and destabilised the speech model.
- **Local context validation retained** - The CI wrapper was removed, while the same context check remains available for local workflow changes.
- **Unsafe seam changes rejected** - Word timestamps destabilised live GPU transcription, and a higher release threshold risked losing short speech.
- **PHP client updated** - The client now records body-safe response counts and retries temporary proxy failures with a short delay.
- **Efficient windowed transcription** - Only new audio is transcribed, removing duplicate output and repeated full-session GPU work.
- **Focused speaker-quality scope** - This release contains phantom-speaker control, clearer confidence, and role-flip damping; seamless identity tracking remains future work.
- **Two-speaker containment** - Extra temporary speaker IDs are merged back into the established Doctor and Patient identities.
- **More balanced workspace** - Transcript, summary, hints, developer tools, and demo controls use the available screen space more effectively.
- **Optional local model service** - The local model starts only when selected, keeping cloud-backed development stacks smaller.
- **Structured agent logs by default** - Local services emit searchable JSON logs, with plain console output still available.
- **Replay uses the live pipeline** - Demo audio now follows the same paced WebSocket and event path as a microphone session.
- **Medical-only agent workflow** - Role inference and summaries now always use Doctor, Patient, and medical-note behaviour.
- **Medical-only interface** - Non-medical mode selection and transport options were removed from the browser.
- **Medical-only demos** - Meeting, interview, broadcast, and lecture scenarios were removed from the demo set.
- **Simpler demo picker** - Generated consultation recordings are selected from one built-in menu instead of separate scenario controls.
- **Curated demo recordings** - Recordings that were unsuitable for the default medical demo set are no longer generated.
- **Full-length replay clips** - Demo consultations now cover the full encounter, with an upper duration limit to protect GPU memory.
- **Focused TypeScript analysis** - Vendored frontend code is excluded so findings cover maintained application code.
- **Smaller frontend modules** - Transcript, replay, summary, and download behaviour were split out of the main recording script.
- **Focused Python analysis** - One-off speech-model experiments are excluded from maintained runtime checks.
- **Smaller Python API module** - Streaming and role queues moved out of the main server module without changing public behaviour.
- **Python behaviour remains the test gate** - Runtime analysis focuses on maintained code while integration behaviour stays covered by tests.
- **Standard PHP complexity checks** - The custom complexity script was replaced with the shared PHP analyser.
- **Clear PHP version bounds** - The project now requires PHP 8.3 within the PHP 8 series and uses a tagged client release.
- **Generated PHP reference excluded** - Generated framework reference data is no longer treated as maintained source code.
- **Stricter PHPUnit runs** - Warnings, deprecations, risky tests, unexpected output, and global state now fail the suite.
- **Joined service logs** - PHP and Python logs share session and request identifiers plus safe timing and delivery details.
- **Pinned speech-model image** - The GPU image and speech toolkit versions are fixed for repeatable builds.
- **Updated Python dependencies** - Supported service and test libraries were raised while keeping the speech model's compatible numeric stack.
- **Consistent WebSocket backend** - Local and container runs now use the same server implementation.
- **Updated PHP dependencies** - Messaging, testing, and framework packages use newer compatible versions within the supported PHP lane.
- **Grounded clinical summaries** - A CPU-only knowledge helper can add short documentation reminders without using the speech GPU.
- **Refreshed scribe design** - The consultation screen has a softer clinical palette, compact controls, and a clearer transcript-summary split.
- **Clear summary states** - The note panel now shows pending, generating, ready, and failed states with guarded retries.
- **Consistent consultation fonts** - The interface and developer logs use the intended UI and monospace typefaces.
- **Compact demo dropdown** - Demo selection now uses a concise consultation menu with upload support.
- **Valid prose timestamps** - Summary instructions now require real minute-and-second ranges and keep row IDs out of bracketed prose.
- **Row corrections keep the badge** - Correcting a row after a visit no longer clears the earned role-confidence state.
- **Streaming review fixes** - Transcript-bearing debug output was removed, empty-start finalisation now drains correctly, and late rows stay chronological.
- **Smaller role-agent requests** - The model receives compact speaker evidence while full transcript rows remain on the server.
- **Suppressed flips stay suppressed** - A rejected role change no longer falls through to a weaker fallback that applies the same change.
- **Isolated agent sessions** - Role and summary agents no longer share conversation state, and clinician overrides win over later suggestions.
- **Persistent manual role changes** - Browser label changes now travel through the application and are stored on the server.
- **Structured agent responses** - Role assignment and note generation use explicit schemas with independent model settings.
- **Useful Python error lines** - Warning and error messages include safe session and error details, with tracebacks where available.
- **Quieter model callbacks** - Internal model reasoning and tool banners no longer spill into container logs.
- **Reliable local model address** - The agent always uses the in-network local-model address instead of a stale host setting.
- **Tighter demo panel spacing** - Upload controls now sit directly below the selector and developer tools use the remaining height.
- **Viewport-sized consultation screen** - Transcript and summary panels scroll internally instead of creating a page-level scrollbar.
- **Compatible cloud libraries** - The GPU image now installs a matching cloud SDK pair and starts without the previous import failure.
- **Bundled local model wiring** - The local model starts with the application when needed and no longer exposes a conflicting host port.
- **Same-origin replay requests** - Replay and summary calls go through the application so browser errors stay valid JSON.
- **Audible replay stop** - Demo audio has a browser player and can be stopped or cancelled before automatic summary generation.
- **Replay follows the audio clock** - Transcript rows appear in step with audible playback and summaries use only visible rows.
- **Large replay uploads supported** - Local upload limits now handle full demo recordings and malformed responses fail cleanly.
- **Transcript empty state fixed** - The start prompt disappears as soon as any transcript rows arrive.
- **Safe frontend rendering** - Transcript, summary, status, and developer text are inserted as text instead of raw HTML.
- **Pinned deployment actions** - Third-party deployment actions use reviewed commit versions.
- **Obvious secret placeholders** - Example environment values no longer resemble real credentials.
- **Configurable health-check secret** - Production health checks require an explicitly configured secret path.
- **Non-medical modes removed** - Meeting, interview, broadcast, lecture, and general workflows were removed from the product.
- **Transcript download removed** - The download button, shortcut, and browser export code were removed.

## v0.2.0 - 2026-03-16

Introduces role mapping, summaries, replay, persistence, reconnect support, stronger quality
checks, and local-first development.

- **Code quality analysers** - Added maintained-code checks for TypeScript, Python, and PHP.
- **Agent-neutral guidance** - Shared instructions can be used by different coding agents without depending on one runtime.
- **CI validation** - Automated checks now validate instruction routing and skill directories.
- **Tool-based role assignment** - The role agent can submit structured speaker mappings while retaining a text fallback.
- **Session summaries** - Visits can produce structured summaries and publish them to the interface.
- **Replay mode** - WAV recordings can be replayed through transcription with pacing, progress, and role inference.
- **Transcript grouping** - Consecutive rows from the same speaker are shown as one readable block.
- **Scenario validation** - Demo scenarios now check duration, expected content, and recording structure.
- **Local model guidance** - Documentation identifies local models that support structured role assignment.
- **Smaller frontend scripts** - Production and developer-only behaviour were moved out of the page template.
- **Developer instrumentation** - Added connection counters, hot reload, scenario coverage, and broader Python tests.
- **Mode-aware roles** - Role prompts can reflect the selected consultation mode.
- **Graceful role fallback** - Role inference falls back from the model to simple rules, then to an unknown result.
- **Manual speaker labels** - Users can correct speaker roles and keep confirmed labels stable.
- **SQLite persistence** - Sessions can use durable SQLite storage or in-memory storage.
- **Reconnect support** - WebSocket and event-stream sessions can resume after brief connection loss.
- **Improved role interface** - Added clipping warnings, shortcuts, audio feedback, flip notices, and accessible transcript labels.
- **Safer session cleanup** - Old sessions are removed and transcript rows carry stable revision identifiers.
- **Local runtime support** - Added a bundled local model, frontend assets, hot reload, and wider integration coverage.
- **PHP 8.3 required** - Local, CI, and deployment environments must use PHP 8.3 or newer within PHP 8.
- **New local model default** - The default changed to a model with reliable tool-calling support.
- **More reliable tool detection** - Role inference recognises successful structured calls without applying the same mapping twice.
- **Clearer role prompt** - The model is asked to use the structured tool first and JSON only as a fallback.
- **Browser-side flip detection** - Visible role changes are measured where the user actually sees them.
- **Simpler developer commands** - Startup options and scenario labels were made clearer.
- **Project-specific agent guidance** - Instructions now reflect this application's boundaries, checks, and release process.
- **Local-first role provider** - New development environments prefer the bundled local model.
- **Recent confidence matters most** - Role confidence uses the latest five decisions instead of the whole session.
- **Better transcript context** - Role inference keeps the opening and most recent wording when input must be shortened.
- **Bounded agent state** - Model responses can include surrounding text, while mapping history remains capped.
- **Bounded inference queue** - Overloaded role work is dropped instead of growing without limit.
- **Lighter runtime internals** - Shared HTTP clients, queues, and audio buffers use simpler and more efficient patterns.
- **Old prompt alias removed** - The unused compatibility setting for the role prompt was deleted.
- **Legacy role stream removed** - The obsolete server-sent role stream and its client code were deleted.
- **Unused event-stream package removed** - Remaining unused server-sent-event support was removed.
- **Unused no-GPU compose file removed** - The application now has one supported GPU-based stack.
- **Instruction names corrected** - Package names, references, and CI triggers now match the live project.
- **Complete session reset** - Reset clears role, summary, replay, confidence, and developer-panel state.
- **Reliable transcript downloads** - Exports collect text from every row in a grouped speaker block.
- **Updated end-to-end contracts** - Integration checks use valid session IDs and current summary and frontend behaviour.
- **Repaired Python tests** - Stale imports, test data, IDs, and audio-buffer expectations were updated.
- **Secure upload files** - Uploaded audio uses generated temporary paths instead of user-shaped names.
- **Validated session IDs** - Malformed IDs are rejected consistently across all API endpoints.
- **Private error delivery** - Browser errors are generic and logged model details are shortened.
- **Frontend and runtime fixes** - Corrected missing browser state, noisy health logs, and configured port display.
- **230 Python unit tests** - Role mapping, summaries, replay, fallbacks, and session behaviour have direct coverage.
- **25 end-to-end checks** - Health, sessions, streaming, uploads, proxies, events, and lifecycle contracts are covered.
- **UUID-only sessions** - Every API endpoint validates session identifiers.
- **Secure temporary files** - Temporary uploads use unpredictable generated paths.
- **Sanitised event errors** - Published errors do not expose internal exception details.
- **No transcript text in logs** - Application logs exclude consultation content.

## v0.1.0 - 2026-03-15

First release of real-time medical transcription with speaker roles, summaries, and developer
testing tools.

- **Transcription interface** - Added live transcript, recording controls, timer, downloads, and reset.
- **Modes and themes** - Added six session modes plus saved light and dark themes.
- **Streaming clients** - Added browser audio streaming, live events, and WebSocket reconnection.
- **Role inference** - Added automatic role labels, past-row relabelling, and confidence badges.
- **Developer panel** - Added scenario, transcript, connection, pipeline, state, and raw-event views.
- **Scenario runner** - Added eight validated scenarios with progress and JSON export.
- **Backend services** - Added application routes, live audio ingest, speech recognition, events, session lifecycle, and role tools.
- **Infrastructure and testing** - Added containers, deployment setup, health checks, load checks, unit tests, and browser-test scaffolding.
- **Reliable local startup** - Startup no longer fails on missing shell variables or helper functions.
- **Retroactive developer updates** - The developer panel now reflects later transcript corrections.
- **Correct stream state** - The browser marks the stream active in the correct order.
- **Working Python hot reload** - The development container now mounts the correct source path.
