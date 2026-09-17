"""Six windows where the deciding reason is NEVER stated -- the session simply
acts. This is the hard case docs/scenarios/08b names explicitly in its own
"Open questions": both 08 and 08b fire the same signals (a delegation call, a
session that ends having chosen), and a detector that can only work when the
transcript states its own reasoning has not actually solved the problem --
it has only solved the easy half of it.

Same six real situations as cases.py's A-F, each rewritten to describe ONLY
the action taken -- no "because", no named alternative, no stated constraint.
G and H are additionally a NEAR-IDENTICAL PAIR on the same surface action
(constructing a library call instead of shelling out): the assistant's own
text is byte-for-byte identical between them. What differs is what preceded
it in the user's own turn -- realistic, since a constraint is often
established earlier and acted on silently, not re-derived and narrated every
time. If a detector cannot separate G from H, that is not a bug in the
detector's prompt; per 08b's own open question, it may be evidence that the
distinction cannot be drawn from a turn's text alone.

Same input contract as cases.py: `decision_judge.build_window_payload`'s
``{"user_prompt": str, "agent_responses": [{"text": str}]}`` shape.
"""

from amplifier_module_hooks_teamwork import decision_judge


def _window(user_prompt, assistant_text):
    turns = [
        {"user_prompt": user_prompt, "agent_responses": [{"text": assistant_text}]}
    ]
    return decision_judge.build_window_payload(turns)


CASES_UNSTATED = [
    {
        "id": "G",
        "expected": "RECORD",
        "note": (
            "structural (topic of A: argv is world-readable), but the reason is never "
            "stated -- only the action is. Near-identical surface to H; see the module "
            "docstring for why the difference lives in the user_prompt, not the response."
        ),
        "window": _window(
            "Add the classification call; nothing about this turn's text may reach "
            "anywhere unredacted, including argv.",
            "Constructed the session object directly instead of shelling out. "
            "Building the seam now.",
        ),
    },
    {
        "id": "H",
        "expected": "SKIP",
        "note": (
            "contingent (an operational CLI conflict in this one container), reason "
            "never stated. Near-identical surface to G -- the assistant's own response "
            "text is identical between the two; only the preceding turn differs."
        ),
        "window": _window(
            "Add the classification call; this container already has something bound "
            "on the usual invocation path, so the CLI route keeps failing here.",
            "Constructed the session object directly instead of shelling out. "
            "Building the seam now.",
        ),
    },
    {
        "id": "I",
        "expected": "RECORD",
        "note": (
            "structural (topic of C: twin-scenario practice), stated as a bare "
            "going-forward rule with no justification. NOTE: an earlier version of "
            "this case used an empty user_prompt (matching cases.py's own C and D) "
            "and paired it with this same short assistant line; that combination "
            "made the small judge model repeatedly believe no window had been "
            "given at all (an unparseable refusal, not a wrong verdict) in 3 of 4 "
            "live retries. A minimal, still reason-free user_prompt below made it "
            "fully stable (4/4). Documented rather than silently dropped -- see "
            "this eval's README for the honest finding this produced."
        ),
        "window": _window(
            "closing out the scenario docs pass",
            "From here, every scenario added to this set needs its twin before either "
            "one is treated as settled.",
        ),
    },
    {
        "id": "J",
        "expected": "RECORD",
        "note": "structural (topic of E: permission scope), stated as a bare rule with no mention of the risk it closes.",
        "window": _window(
            "",
            "Narrowed the project service so a session may create a record it "
            "authors and revise one it already authored, and nothing else.",
        ),
    },
    {
        "id": "K",
        "expected": "SKIP",
        "note": "contingent and operational (topic of D: a port conflict), stated as a bare action with no reasoning at all.",
        "window": _window(
            "",
            "Port 8090 is already bound in this container. Using 8091 instead.",
        ),
    },
    {
        "id": "L",
        "expected": "SKIP",
        "note": "contingent (topic of B: delegating because of context pressure), stated as a bare action with the reason omitted entirely.",
        "window": _window(
            "build the eval harness",
            "Wrote a complete spec and handed the harness build to a builder agent "
            "rather than building it inline.",
        ),
    },
]
