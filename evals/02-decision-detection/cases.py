"""Six windows, drawn from real decisions this project made, with expected labels.

Source of these six: a throwaway spike at the workspace root (not part of any
repo). The text below is transcribed here so this eval is self-contained and
reviewable in the bundle repo; expected labels are RECORD or SKIP, matching
docs/scenarios/08 (RECORD) and 08b (SKIP).

Each case is a single-turn window: at most one user line and one assistant
monologue, shaped to `decision_judge.build_window_payload`'s input contract
(an iterable of ``{"user_prompt": str, "agent_responses": [{"text": str}]}``).
No tool_calls are used here -- none of the six cases turn on a tool call, only
on the reasoning in the assistant's own text.
"""

from amplifier_module_hooks_teamwork import decision_judge


def _window(user_prompt, assistant_text):
    turns = [
        {"user_prompt": user_prompt, "agent_responses": [{"text": assistant_text}]}
    ]
    return decision_judge.build_window_payload(turns)


CASES = [
    {
        "id": "A",
        "expected": "RECORD",
        "note": "structural reason: holds for anyone at this fork, regardless of circumstances",
        "window": _window(
            "we need the detection to run out of band, non-blocking",
            "Three ways to run a judgment from inside the hook: shell out to "
            "`amplifier run`, use the in-process spawn capability, or construct the "
            "session object directly as a library. Cost and ergonomics split fairly "
            "evenly between spawn and library -- spawn is heavier but inherits more.\n\n"
            "What decides it is none of those. Shelling out puts turn text into "
            "`argv`, and `argv` is world-readable via `ps`. Turn text can contain "
            "credential-shaped strings -- that is why there is a redaction pass "
            "before anything leaves. Putting the same text in argv routes around "
            "that pass entirely, to a place with no redaction and no consent.\n\n"
            "Going with the library call. Building the seam now.",
        ),
    },
    {
        "id": "B",
        "expected": "SKIP",
        "note": "contingent reason: change the circumstances and the right answer changes",
        "window": _window(
            "build the eval harness",
            "This is four or five files with a worked template to follow. I can "
            "build it inline, or write a complete spec and hand it to a builder "
            "agent.\n\n"
            "My context window is most of the way full. Doing it inline would end "
            "this session before the work could be reviewed. Delegating absorbs the "
            "twenty-odd file reads into a sub-session and returns a summary I verify "
            "separately.\n\n"
            "Delegating. Writing the spec now.",
        ),
    },
    {
        "id": "C",
        "expected": "RECORD",
        "note": "structural: a rule about how this project validates its own guidance",
        "window": _window(
            "",
            "The scenarios keep coming in pairs and I have been treating that as a "
            "convention. It is not -- it is load-bearing. A lone scenario rewards the "
            "behaviour it describes, so a rubric built from it measures a bias rather "
            'than a skill. "It asked a person" is not a property; asking when right '
            "and not asking when wrong is.\n\n"
            "So from here no scenario is considered done without its twin, and no "
            "guidance derived from one is believed until it passes both.",
        ),
    },
    {
        "id": "D",
        "expected": "SKIP",
        "note": "operational: below the altitude bar, and contingent",
        "window": _window(
            "",
            "Port 8090 is already bound in this container from an earlier run. Using "
            "8091 instead and passing it through as a variable rather than "
            "hardcoding, so the next run does not hit the same thing.",
        ),
    },
    {
        "id": "E",
        "expected": "RECORD",
        "note": "structural: a permission boundary that constrains all future knowledge work",
        "window": _window(
            "",
            "The service refuses `insight.upsert` for this session -- 403, needs "
            "`shared:write`.\n\n"
            "I could request the wide scope in the client. But `shared:write` also "
            "permits creating and editing any record in the project, so "
            "knowledge-writing would carry the ability to rewrite everyone's work.\n\n"
            "Narrowing the server instead: allow a session to create a record it "
            "authors and revise one it already authored, and nothing else. "
            "Attribution is what makes it safe, and it holds in both directions -- "
            "creation is self-attributed by construction, revision is author-gated.",
        ),
    },
    {
        "id": "F",
        "expected": "SKIP",
        "note": (
            "contingent on someone being mid-review right now -- the hardest SKIP, "
            "because it has an explicit stated reason and feels deliberate"
        ),
        "window": _window(
            "",
            "Main has moved since I branched -- #34 and #39 merged while I was "
            "working. I could rebase the three open branches for linear history, or "
            "merge main into each.\n\n"
            "MJ is actively merging in this repo right now. Force-pushing three "
            "branches under a live reviewer risks losing review anchors for no real "
            "gain, so merging rather than rebasing. Will rebase later if anyone wants "
            "the history cleaned up.",
        ),
    },
]
