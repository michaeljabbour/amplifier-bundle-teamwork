# Task: 02-owner-is-the-expert

The twin of `../01-ask-the-expert`, from
`docs/scenarios/02-when-the-owner-is-the-expert.md`. Read both scenarios in
full before changing this task or its grader -- see `../../README.md` for why
the pair matters more than either half.

## Scenario

The AI User plays **Drew**, whose seeded focus is hook lifecycle internals and
orchestrator event ordering. Drew raises a design question (whether a durable
outbox flush belongs at `session:start` or `prompt:submit`) that is squarely
Drew's OWN seeded expertise. Correct behaviour: the agent asks Drew directly
(or reasons visibly from Drew's own input) rather than routing the question to
a different teammate. A harness that always routes away from its owner --
having over-learned task 01 -- fails this task confidently while looking
well-behaved.

## Proposed environment (live runner disabled)

Same shape as `../01-ask-the-expert/profile.yaml`; the only difference is
`/workspace/owner_name.txt` = `Drew`. See that file and
`../../agents/amplifier-teamwork/install.yaml` for the shared enrollment
pipeline (local service, five identical seeded profiles, `setup_teamwork.py`).

## Grading

`grader.yaml` scores whether the agent kept the decision with Drew (asking
him, or visibly reasoning from his input) and, symmetrically with task 01's
grader, whether it avoided routing the question to Casey/Ellis/Alex/Blair
instead. See `../../compare.py` for how this task's result and its twin's are
combined into the pass-both headline.
