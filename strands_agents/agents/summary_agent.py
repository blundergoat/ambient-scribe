"""
Strands summary agent for the browser's final consultation summary.

When the user ends a session, this agent reads the role-attributed transcript
and drafts SOAP-style sections for review. It uses Bedrock or CPU-only Ollama,
never the NeMo GPU, and each call gets a fresh Agent so one visit's transcript
cannot remain in another visit's conversation history.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

SUMMARY_AGENT_MODEL_PROVIDER = os.environ.get(
    "SUMMARY_AGENT_MODEL_PROVIDER",
    os.environ.get("ROLE_AGENT_MODEL_PROVIDER", "bedrock"),
)
SUMMARY_AGENT_MODEL_ID = os.environ.get(
    "SUMMARY_AGENT_MODEL_ID",
    os.environ.get(
        "ROLE_AGENT_MODEL_ID",
        "au.anthropic.claude-haiku-4-5-20251001-v1:0",
    ),
)
SUMMARY_AGENT_OLLAMA_MODEL = os.environ.get(
    "SUMMARY_AGENT_OLLAMA_MODEL",
    os.environ.get("ROLE_AGENT_OLLAMA_MODEL", "qwen3.5:9b"),
)
# 8192 gives the fidelity retry 2x headroom over the largest observed clean
# draft (3,923 tokens, consult 5.3); the model's output ceiling is 64K, and
# 4096 truncated that visit's retry in three independent runs (token-cap blocker B3).
SUMMARY_AGENT_MAX_TOKENS = int(os.environ.get("SUMMARY_AGENT_MAX_TOKENS", "8192"))

_SHARED_SUMMARY_RULES = """
Rules:
- Write like a clinician, not a transcriber. Each SECTION contains 3-6 concise clinical
  sentences (claims), clinically ordered: presenting complaint first, then history and
  characteristics, then associated symptoms and screening answers, then psychosocial and
  functional impact. Each claim synthesises the RELATED facts of one theme (for example,
  all sleep findings in one claim) and cites EVERY source unit that supports any part of
  it. Never write one sentence per transcript utterance, and never bundle UNRELATED
  themes into one claim - one theme, one claim.
- Key Points are 3-5 bullets. Each bullet is ONE decision-relevant takeaway on ONE theme
  - never merge unrelated themes into one bullet to save space. Key Points follow every
  fidelity rule in this list, especially hedges: a patient's "probably coincides with"
  must never compress into "triggered by" or "caused by".
- Prefer the patient's own symptom words over clinical paraphrase ("heart racing", not
  "palpitations"; "worried", not "racing thoughts") unless the clinician used the term.
- Do not start every sentence with "Patient reports": the Subjective section is
  implicitly patient-reported. Attribute explicitly only where the speaker matters
  (a relative's account, or contrast with the clinician's observation).
- Document pertinent negatives for screening questions the clinician asked and the
  patient answered (sleep, appetite, mood, risk), preserving the patient's certainty
  exactly ("probably no significant change in eating") - never firmer than spoken, and
  only when the patient's answer exists in the transcript.
- Cite evidence ONLY as source unit IDs from the prompt's unit list, in each claim's
  `source_unit_ids` array. COPY each ID exactly as it appears in the list, character for
  character - never retype from memory, never merge two IDs into one, never invent an ID,
  and never cite row, segment, or timestamp identifiers. A claim synthesising several
  units lists each unit's ID separately.
- Set each claim's `evidence_basis`: `source_unit` when citing units, `transcript_absence`
  for a bounded negative supported by what the transcript covers, or `none` when no evidence
  exists. Claims with basis `transcript_absence` or `none` leave `source_unit_ids` empty.
- Prose contains no bracketed references of any kind; provenance lives only in
  `source_unit_ids`.
- Keep the summary concise - aim for 200-400 words.
- Use the speaker role names (DOCTOR, PATIENT, etc.), not raw speaker IDs.
- A partial or interrupted transcript is still summarised: write claims for the clinical
  content it does cover (typically the Subjective history) and state plainly, per section,
  when nothing is documented yet. Declare the visit unsummarisable ONLY when the transcript
  contains no clinical content at all (for example, only greetings or identity
  confirmation) - never merely because examination, assessment, or plan are missing.
- Never assert a clinical fact the transcript does not support.
- When the patient expresses uncertainty ("I don't know", "maybe", "not sure"), document the
  point explicitly as unclear or not established - never resolve it to one side, and never
  infer the answer from surrounding phrasing (a patient answering "I don't know really, it
  just happened" to a sudden-vs-gradual question means onset is UNKNOWN, not sudden).
- When words, names, or answers are missing, cut off, or unintelligible, say the material was
  not clearly captured or not documented. Never say the patient was unsure, unable to recall,
  did not know, or declined to answer unless the patient's own words establish that state.
  Apply record-limitation wording only to missing material; do not hedge facts that are clear.
- Reserve straight or typographic single/double quotation marks for an
  exact contiguous phrase in the transcript. Leave paraphrases and inferred names unquoted.
- Clinical characteristics (onset, severity, laterality, timing) appear only as the speaker
  stated them, preserving the speaker's own certainty.
- Record a negative finding only when the patient explicitly denied it or it was examined;
  absence of mention is not a negative finding, and a clinician question or statement the
  patient never answered is not a denial (transcripts often end mid-question - an unanswered
  "your breathing is okay?" establishes nothing about breathing).
- Patient answers to screening questions are reported history, never examination findings -
  nothing is "intact" or "normal on examination" unless an examination was performed.
- Use the structured summary schema only. No prose outside the result.

Output format:
{
    "title": "Brief session title",
    "sections": [
        {
            "heading": "Section Name",
            "claims": [
                {
                    "text": "One concise clinical sentence synthesising the related findings of one theme.",
                    "evidence_basis": "source_unit",
                    "source_unit_ids": ["unit-0416-0423"]
                }
            ]
        }
    ],
    "key_points": [
        {
            "text": "One concise key point.",
            "evidence_basis": "source_unit",
            "source_unit_ids": ["unit-0002-0009"]
        }
    ]
}
"""

MEDICAL_SUMMARY_PROMPT = f"""You are a medical documentation agent.

You receive a role-attributed consultation transcript. It may cover only part
of the visit - recordings can stop mid-consultation - so treat it as everything
captured so far, not as proof the visit is over.
Generate a SOAP note summarising what the transcript covers.

Required sections:
- **Subjective**: Patient's chief complaint, symptoms, history as reported. State each
  clinical characteristic (onset, severity, timing) exactly as the patient answered - if
  the patient was unsure or did not know, write that the patient was unsure. Never restate
  the clinician's question wording as if it were the patient's answer.
- **Objective**: Only clinician-performed examination findings, vitals, or observations.
  Patient-reported symptoms belong in Subjective. If no examination is documented, say so.
- **Assessment**: Only diagnoses or differentials the clinician stated. Never add
  AI-inferred diagnoses, suggested conditions, or unstated rule-outs. If the clinician
  stated none, say no assessment was documented.
- **Plan**: Prescribed treatment, follow-up instructions, referrals

If the doctor did not explicitly state an assessment or plan, say that none was documented;
you may summarise what was discussed without converting it into a diagnosis or plan.
{_SHARED_SUMMARY_RULES}"""


def create_summary_agent():
    """Create the isolated agent used after the user requests a note.

    Use once per visit so prior consultation text never reaches the next note.

    Returns:
        Agent configured for off-GPU summary generation and isolated history.

    Raises:
        RuntimeError: When the configured Bedrock or Ollama model cannot be created.
    """
    try:
        from strands import Agent

        summary_model = _create_summary_model()

        return Agent(
            model=summary_model,
            tools=[],
            system_prompt=MEDICAL_SUMMARY_PROMPT,
            callback_handler=None,
            name="summary",
            agent_id="ambient-scribe-summary",
            trace_attributes={"scribe.specialty": "medical"},
        )
    # Example: the clinician clicks Summarise while the configured provider is unavailable.
    except Exception as provider_error:
        raise RuntimeError(
            f"Failed to create summary agent: {provider_error}"
        ) from provider_error


def _create_summary_model():
    """Select the off-GPU model that drafts the clinician-facing note.

    Use while handling a summary request; NeMo remains reserved for transcription.

    Returns:
        Bedrock or CPU-only Ollama model; never a GPU-backed local model.
    """
    # Bedrock is the default path for clinician-ready final summaries.
    if SUMMARY_AGENT_MODEL_PROVIDER == "bedrock":
        from strands.models.bedrock import BedrockModel

        return BedrockModel(
            model_id=SUMMARY_AGENT_MODEL_ID,
            region_name=os.environ.get("AWS_DEFAULT_REGION") or "ap-southeast-2",
            streaming=True,
            max_tokens=SUMMARY_AGENT_MAX_TOKENS,
        )
    # Ollama remains CPU-only for local development because NeMo owns the GPU.
    elif SUMMARY_AGENT_MODEL_PROVIDER == "ollama":
        from strands.models.ollama import OllamaModel

        return OllamaModel(
            host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            model_id=SUMMARY_AGENT_OLLAMA_MODEL,
            max_tokens=SUMMARY_AGENT_MAX_TOKENS,
        )
    else:
        raise ValueError(
            f"Unknown SUMMARY_AGENT_MODEL_PROVIDER: {SUMMARY_AGENT_MODEL_PROVIDER}. "
            "Use 'bedrock' or 'ollama'."
        )
