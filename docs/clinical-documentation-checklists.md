# Clinical Documentation Checklists

Project-authored documentation checklists for Ambient Scribe summary
grounding. These are prompt-context reminders about what a consultation
note should record; they are not clinical guidance and never override the
transcript. Each section anchor is cited by one card in
`strands_agents/data/clinical_knowledge.json`, and the knowledge asset
binds this file's SHA-256, so edit both together and re-run
`scripts/clinical-data-audit.py`.

<a id="allergy-antihistamine-plan"></a>

## Allergic rash and antihistamine plan

When an allergic rash is discussed, document the suspected trigger, the antihistamine chosen with dose and duration, and the escalation advice if swelling, breathing difficulty, or spreading symptoms develop.

Retrieval keywords: `antihistamine`, `hives`, `loratadine`.

<a id="antibiotic-stewardship"></a>

## Antibiotic course documentation

When an antibiotic is started, changed, or deferred, document the indication, the agent with dose and course length, any delayed prescription instructions, and the safety-net advice for worsening symptoms.

Retrieval keywords: `amoxicillin`, `antibiotic course`, `delayed prescription`.

<a id="anticoagulant-review"></a>

## Anticoagulant review

When anticoagulation is discussed, document the indication, the agent and dose, renal function and interaction checks where relevant, any bleeding-risk discussion, and the monitoring or follow-up plan.

Retrieval keywords: `apixaban`, `bleeding risk`, `warfarin`.

<a id="asthma-inhaler-review"></a>

## Asthma inhaler and action plan

When asthma control is reviewed, document reliever frequency, the inhaler technique check, peak-flow or symptom trend where available, and whether the written asthma action plan was updated.

Retrieval keywords: `inhaler technique`, `peak flow`, `salbutamol`.

<a id="chest-pain-objective"></a>

## Chest pain documentation

Chest-pain documentation should capture symptom history, relevant risk factors, objective assessment such as ECG and vitals, and the follow-up or escalation plan.

Retrieval keywords: `chest pain`, `ecg`, `troponin`.

<a id="diabetes-medication-review"></a>

## Diabetes medication review

When diabetes medicines are started or changed, document the recent glucose pattern, renal function considerations for the chosen agent, sick-day advice where relevant, and the follow-up and repeat-testing timing.

Retrieval keywords: `blood glucose`, `empagliflozin`, `metformin`.

<a id="fatigue-workup"></a>

## Persistent tiredness workup

When persistent tiredness is assessed, document sleep pattern, mood screen, red-flag review, relevant baseline tests such as iron studies and thyroid function, and the agreed review interval.

Retrieval keywords: `iron studies`, `tired all the time`.

<a id="mental-health-safety-plan"></a>

## Mental health review and safety plan

When low mood or anxiety is discussed, document the screening outcome, any risk assessment and safety-planning discussion, agreed supports or referrals, and the review timing.

Retrieval keywords: `anxious`, `mood`, `safety plan`.

<a id="nsaid-ace-renal"></a>

## NSAID and ACE inhibitor review

When an NSAID is discussed alongside an ACE inhibitor, document the renal-risk review, the blood-pressure and renal function monitoring plan, and the safety-net advice that was given.

Retrieval keywords: `ibuprofen`, `lisinopril`, `naproxen`, `renal function`.

<a id="skin-infection-redflags"></a>

## Skin infection red flags

When a skin infection is suspected, document the affected area and any marked border, systemic features such as fever, the chosen treatment, and the explicit advice about worsening pain or feeling unwell.

Retrieval keywords: `cellulitis`, `spreading redness`.

<a id="thyroid-monitoring"></a>

## Thyroid replacement monitoring

When thyroid replacement is reviewed, document the current dose, the symptom trend, the latest TSH result where mentioned, and the planned timing of repeat thyroid function testing.

Retrieval keywords: `levothyroxine`, `tsh`.
