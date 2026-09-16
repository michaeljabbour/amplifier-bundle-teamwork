"""Six windows, grounded in real material from this project, with expected labels.

Three RECORD cases are drawn from real incidents in this repository: scenario
06's own running example (the compiled-extension/grep miss), the Origin-header
root cause behind PR #15 (docs/scenarios and SCRATCH.md's `teamwork-cio`
entry), and the `#subdirectory=` namespace-rooting defect documented in
`behaviors/teamwork.yaml`'s own comments and SCRATCH.md's `teamwork-x8a` entry.

Three SKIP cases are 06b-shaped: something true was observed, generalises easily,
and is wrong because the evidence is bounded to one environment. Case D is
docs/scenarios/06b-the-lesson-that-is-only-true-on-your-machine.md's OWN
canonical example (two suites, a clean checkout, node_modules never
installed, CI green throughout), transcribed here per this eval's fidelity
rule rather than invented from scratch. Cases E and F are constructed but
grounded in this project's own real, documented conventions (ANTHROPIC_API_KEY
/ ~/.amplifier/keys.env from AGENTS.md and SCRATCH.md; the `--noproxy '*'`
requirement from AGENTS.md's own gotchas table) -- the same "throwaway spike,
grounded in real project shape" provenance evals/02-decision-detection/cases.py
itself documents for its six cases.

Each case is a single-turn window: at most one user line and one assistant
monologue, shaped to `decision_judge.build_window_payload`'s input contract
(an iterable of ``{"user_prompt": str, "agent_responses": [{"text": str}]}``).
No tool_calls are used here -- lesson detection has no tool:pre trigger (see
__init__.py's on_end() comment), so no case turns on one.

Deliberately NOT lexically identical to the judge's own rule text
(`decision_judge._LESSON_RULE`) even where a case is drawn from the same
scenario that rule quotes -- e.g. case A paraphrases 06's "bounded by what
the search can see" rather than repeating it verbatim, so the eval tests
transfer of the underlying judgment rather than string matching against the
prompt the judge was given.
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
        "note": (
            "structural: an exhaustive-looking text search is bounded by what it can "
            "parse, regardless of whose machine or which version -- drawn from "
            "docs/scenarios/06's own running example"
        ),
        "window": _window(
            "",
            "Closing this item now that I actually ran it: the field renders on "
            "this version, so the earlier claim -- \"nothing consumes this "
            "field\" -- doesn't hold. The original filing grepped every .py "
            "file in the package and found only the field's own definition; "
            "that grep was careful and it was still wrong, because the actual "
            "consumer is a compiled extension module, not Python source at "
            "all.\n\n"
            "The fact that THIS field is consumed is true for this version and "
            "this item -- that belongs in the task, and it will go stale the "
            "moment the field changes again. What generalises is the reason "
            "the search missed it: an exhaustive-looking check of a package's "
            "source is only exhaustive over the language that check can "
            "parse. If a package ships a compiled artifact anywhere in its "
            "dependency graph, a text search of the interpreted source will "
            "silently pass over whatever lives inside it, on any machine, on "
            "any future version. Recording that on its own, separate from "
            "this item.",
        ),
    },
    {
        "id": "B",
        "expected": "RECORD",
        "note": (
            "structural: the service's own auth gate checks Origin before "
            "credentials, true for any future caller regardless of machine -- "
            "drawn from this project's real PR #15 / teamwork-cio finding"
        ),
        "window": _window(
            "",
            "Root cause of the 403 on /api/login: the request sent "
            "`Origin: <api host>`. Swapping to `Origin: <web host>` instead "
            "got a 401 (bad credentials, which is the honest failure once "
            "Origin stops being the blocker), confirmed three separate times "
            "with only Origin changing.\n\n"
            "The fix for THIS enrollment script is one line, and that's local "
            "to the item. But the reason is not specific to this script: "
            "this service's own auth gate checks Origin against the WEB host "
            "before it ever looks at credentials, regardless of which script "
            "or which machine is making the call -- so anything that "
            "authenticates against this service's member plane needs to send "
            "the web origin, never the api origin, or it will be rejected "
            "before its credentials are even read. That's true of any future "
            "caller hitting this same service, not just this one.",
        ),
    },
    {
        "id": "C",
        "expected": "RECORD",
        "note": (
            "structural: a #subdirectory= install roots every namespace "
            "reference at that subdirectory, and a resolve-to-nothing include "
            "fails silently -- true for any bundle author, not this one file -- "
            "drawn from this project's real teamwork-x8a finding"
        ),
        "window": _window(
            "",
            "Tracked down why the skill never showed up in the catalog after "
            "installing via `bundle add <url>#subdirectory=behaviors/teamwork.yaml`: "
            "the tool config used `@teamwork:skills`, and that namespace roots "
            "at `behaviors/` when the bundle is loaded from that subdirectory "
            "form -- not at the repo root. So `@teamwork:skills` was resolving "
            "to `behaviors/skills`, which doesn't exist, and a context/tool "
            "include that resolves to nothing is dropped without any error. "
            "Switched to a path relative to the yaml file itself and it "
            "started working.\n\n"
            "The specific broken line is fixed and belongs in this change. "
            "But the failure mode generalises past this one bundle: whenever "
            "a bundle is installed via a `#subdirectory=<path>` fragment, "
            "every namespace-style reference inside that subdirectory's files "
            "roots at the subdirectory, not the repo root -- and because a "
            "resolve-to-nothing include fails silently, this kind of mistake "
            "gives no error at all, just a missing capability. Worth a rule "
            "for anyone authoring bundle files meant to be installed this "
            "way, not just a fix to this one bundle.",
        ),
    },
    {
        "id": "D",
        "expected": "SKIP",
        "note": (
            "the required 06b-shaped case: real observation, wrong because it "
            "generalises across machines -- transcribed from "
            "docs/scenarios/06b's own canonical example (node_modules never "
            "installed; CI was green the whole time)"
        ),
        "window": _window(
            "",
            "Ran this repo's test suite before making any changes and two UI "
            "suites failed immediately. To make sure it isn't something in my "
            "own branch, stashed my change and reran against a completely "
            "clean checkout -- same two failures, so this isn't anything I "
            "did.\n\n"
            "That means the build is red on main right now, and it explains "
            "why work in this area has felt slow. Telling the project so "
            "nobody else spends twenty minutes chasing something that isn't "
            "their own doing.",
        ),
    },
    {
        "id": "E",
        "expected": "SKIP",
        "note": (
            "environment-scoped: a missing key in THIS container is read as "
            "'the harness can't reach a provider at all' -- constructed but "
            "grounded in this project's real ANTHROPIC_API_KEY / "
            "~/.amplifier/keys.env convention (AGENTS.md, SCRATCH.md)"
        ),
        "window": _window(
            "",
            "Tried to run the eval script inside this DTU and it printed "
            "\"ANTHROPIC_API_KEY is not set\" and exited without doing "
            "anything. Checked twice -- the variable really isn't in this "
            "container's environment, and ~/.amplifier/keys.env doesn't exist "
            "here either.\n\n"
            "So this eval harness can't reach a real provider at all right "
            "now; anyone trying to run it is going to hit the exact same "
            "wall until that's fixed at the source. Worth a note so the next "
            "person doesn't waste a run finding this out the hard way.",
        ),
    },
    {
        "id": "F",
        "expected": "SKIP",
        "note": (
            "environment-scoped: this container's own network configuration "
            "is read as a service-wide outage -- constructed but grounded in "
            "this project's real `--noproxy '*'` requirement (AGENTS.md's "
            "gotchas table)"
        ),
        "window": _window(
            "",
            "Tried to reach the API for status and got connection refused on "
            "every attempt -- not a 403, not a timeout, just refused "
            "outright, three tries in a row.\n\n"
            "So the whole staging environment for this service looks to be "
            "down right now. Flagging it as an outage before anyone else "
            "burns time on it.",
        ),
    },
]
