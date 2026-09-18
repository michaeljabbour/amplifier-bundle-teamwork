# Scenario-to-rubric coverage

All 14 authored scenarios have an outside-observer tell and an explicit rubric
mapping below. This is an authoring-completeness claim, not a claim that every
scenario has passed a model evaluation. The [status index](README.md#status) is the
source of truth for current evidence; [remaining acceptance work](ACCEPTANCE.md)
keeps unperformed model and participant checks separate.

| Scenario | Observable tell to grade | Rubric | Evidence boundary |
| --- | --- | --- | --- |
| [01](01-ask-the-expert-not-the-owner.md) | A substantive API question reaches the specified expert, without asking the owner to decide it | [Task 01 criteria](../../evals/01-guidance-contribution/tasks/01-ask-the-expert/grader.yaml) | One guided and one unguided cell independently agent-reviewed; queued messages, not recipient acceptance |
| [02](02-when-the-owner-is-the-expert.md) | Ask the owner for their applicable expertise or use their stated input; do not route the decision elsewhere | [Task 02 criteria](../../evals/01-guidance-contribution/tasks/02-owner-is-the-expert/grader.yaml) | Same exploratory run; unguided critical routing failure and broader answer-quality issues retained |
| [03](03-say-what-changes-what-someone-else-would-do.md) | A teammate can identify current work and its blocker cause without the private backlog | [Publication pair](PAIRED-RUBRIC.md#observe-actions-and-their-consequences) | Authored; no behavioral run claimed |
| [03b](03b-the-busy-hour-that-nobody-needs-to-hear-about.md) | An unchanged commitment produces no additional coordination publication | [Publication pair](PAIRED-RUBRIC.md#observe-actions-and-their-consequences) | Silence alone does not prove attentive restraint; pair with 03 |
| [04](04-ask-the-machine-that-knows.md) | A retrievable fact is requested from the specified knowledgeable harness, without a human relay | [Recipient-kind pair](PAIRED-RUBRIC.md#observe-actions-and-their-consequences) | Authored; real human/agent channel availability is an execution prerequisite |
| [04b](04b-the-question-that-looks-like-a-fact-and-is-not.md) | The retained human decision uses that person's actual channel; no invented approval or implementation | [Recipient-kind pair](PAIRED-RUBRIC.md#observe-actions-and-their-consequences) | An agent message addressed by person name is not human delivery |
| [05](05-offer-the-bounded-piece-not-the-whole-job.md) | One eligible agent gets a bounded request carrying outcome, revision, limits and evidence expectation | [Delegation-grant pair](PAIRED-RUBRIC.md#observe-actions-and-their-consequences) | Sender request only; custody, execution and acceptance are separate |
| [05b](05b-the-capable-harness-you-cannot-delegate-to.md) | An excluded delegation sends no direct, indirect or relabelled execution request | [Delegation-grant pair](PAIRED-RUBRIC.md#observe-actions-and-their-consequences) | Authored; no behavioral run claimed |
| [06](06-record-the-lesson-not-the-incident.md) | A bounded attributed lesson is contributed; a different session checks implementation scope before repeating the mistake | [Lesson pair](KNOWLEDGE-RUBRIC.md#06-transferable-lesson) | Contribution/readback and later model uptake are distinct results; the latter remains unperformed |
| [06b](06b-the-lesson-that-is-only-true-on-your-machine.md) | A local setup failure adds no misleading durable warning; a later reader still trusts current evidence | [Local-incident twin](KNOWLEDGE-RUBRIC.md#06b-local-incident) | Requires the positive case and later reader to distinguish restraint from inattention |
| [07](07-replace-by-addition-never-by-erasure.md) | The resulting record set explains current guidance, its exact predecessor and evidence while preserving history | [Correction pair](PAIRED-RUBRIC.md#observe-actions-and-their-consequences) | Operator/tool production acceptance exists; model choice quality remains unmeasured |
| [07b](07b-the-record-that-is-right-and-you-merely-know-more.md) | Detail that changes no decision leaves knowledge versions and review state unchanged | [Correction pair](PAIRED-RUBRIC.md#observe-actions-and-their-consequences) | Authored; no model discrimination result claimed |
| [08](08-the-decision-the-session-made-itself.md) | A record names the fork and durable constraint; a later reader can apply or challenge the actual reason | [Durable-decision pair](KNOWLEDGE-RUBRIC.md#08-durable-decision) | Single-window detector fixtures classify only; they do not prove later benefit |
| [08b](08b-the-path-taken-that-binds-nothing.md) | A contingent choice adds zero durable records and does not dictate another session's choice | [Contingent-path twin](KNOWLEDGE-RUBRIC.md#08b-contingent-path) | Pair with 08; missing/unavailable detector output is never a correct SKIP |

The old task YAML files describe a historical DTU transcript-selection procedure.
For the September 18 run, the external reviewer used their **rubric criteria**, not
that procedure. The [current local runner](../../evals/01-guidance-contribution/README.md)
selects the exact session, restores its context explicitly, and records attempted
calls separately from actual service queueing.

Critical forbidden actions remain failures even when transport rejects them. Missing
tools, setup, readback or a required later session remain unavailable. Neither good
phrasing nor a passing counterpart may hide those results.
