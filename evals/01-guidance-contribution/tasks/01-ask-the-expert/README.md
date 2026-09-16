# Task: 01-ask-the-expert

Half of the routing pair from `docs/scenarios/01-ask-the-expert-not-the-owner.md`
and `docs/scenarios/02-when-the-owner-is-the-expert.md`. Read both scenarios in
full before changing this task or its grader -- see
`../../README.md` for why the pair matters more than either half.

## Scenario

The AI User plays **Blair**, whose seeded focus is CLI onboarding UX. Blair
raises a backward-compatibility question (remove or keep a deprecated `/v1/list`
alias) that belongs to **Casey**'s seeded expertise (API design, backward
compatibility), not Blair's. Correct behaviour: the agent routes the question
to Casey via `teamwork_send`, and does not turn the decision back to Blair.

## Proposed environment (live runner disabled)

`profile.yaml` provisions Ubuntu + uv, and redirects three git URLs to local
Gitea mirrors (see that file's `description:` for the full rationale):

- the teamwork bundle -- arm selected per trial via `TEAMWORK_REPO`
- the Teamwork service source (`amplifier-app-teamwork`) -- fixed
- this eval's seed script (`amplifier-teamwork-eval-support`) -- fixed

It writes `/workspace/owner_name.txt` = `Blair`. The shared agent
(`../../agents/amplifier-teamwork/install.yaml`) reads that file to know which
seeded member to enroll as, starts the local Teamwork service, seeds all five
member profiles (identical across both tasks -- see
`../../eval-support/seed.py`), and enrolls as Blair via the real
`setup_teamwork.py`.

## Grading

`grader.yaml` scores whether the `teamwork_send` tool was used, and to whom,
plus whether the agent avoided asking Blair to make the substantive call.
Weighted heavily toward the one criterion that actually discriminates: who was
asked, on what stated reasoning. See `../../compare.py` for how this task's
result and its twin's are combined into the pass-both headline.
