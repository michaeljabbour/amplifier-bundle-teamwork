"""Event identifiers emitted by hooks-teamwork on the kernel's hook bus.

WHY THESE EXIST. Detection's only observable result used to be a successful
publish: `_judge_and_record` ends in a RecordInsightTool write, and every other
outcome went to a local SQLite table with no subscriber. Eleven distinct signals
about whether the detector works, already computed, and unreadable by anyone --
which is why "do we detect decisions?" had no answer. A mechanism whose failures
are invisible can only be said to have shipped, not to work.

WHAT A CONSUMER GETS. Three outcomes per detector, because three is what a
consumer does something different about:

  recorded  something new exists on the shared project
  skipped   the detector ran and deliberately produced nothing
  failed    the detector did not complete, or its write did not land

The raw outcome string always travels in the payload, so categorising loses
nothing: a dashboard can count `failed`, and an audit can still read exactly
which failure it was.

DISCOVERY, NOT HARD-CODED NAMES. `mount()` contributes ALL_EVENTS to the
`observability.events` channel, the same channel `amplifier-bundle-modes`
contributes to and `hook-context-intelligence` already consumes -- it calls
`collect_contributions("observability.events")` and registers a handler per
discovered name. So a consumer needs no change to receive these, and this module
stays the single source of truth for what it emits.
"""

DECISION_RECORDED = "teamwork:decision_recorded"
DECISION_SKIPPED = "teamwork:decision_skipped"
DECISION_FAILED = "teamwork:decision_failed"
LESSON_RECORDED = "teamwork:lesson_recorded"
LESSON_SKIPPED = "teamwork:lesson_skipped"
LESSON_FAILED = "teamwork:lesson_failed"

ALL_EVENTS = [
    DECISION_RECORDED,
    DECISION_SKIPPED,
    DECISION_FAILED,
    LESSON_RECORDED,
    LESSON_SKIPPED,
    LESSON_FAILED,
]

# Every outcome the detector can reach, and which of the three it is.
#
# EXHAUSTIVE ON PURPOSE, and a test asserts it stays that way against the
# outcomes the module actually records. An outcome missing from here emits
# nothing, so the table not matching the code is the one way this surface can
# silently lose a signal -- which is the exact failure it exists to end.
OUTCOMES = {
    # Something new exists on the shared project.
    "recorded": "recorded",
    # The detector ran and deliberately produced nothing.
    "skipped_deliberate": "skipped",      # the window was recorded by hand
    "no_claim": "skipped",                # the judge found nothing worth saying
    "duplicate_fingerprint": "skipped",   # already recorded or reserved
    "binding_changed": "skipped",         # the session rebound mid-detection
    # The detector did not complete, or its write did not land.
    "judge_failed": "failed",
    "judge_cancelled": "failed",
    "unusable_shape": "failed",           # the judge returned a verdict we cannot use
    "write_cancelled": "failed",
    "write_raised": "failed",
    "refused": "failed",                  # the service refused the record
    "acceptance_unknown": "failed",       # the write may or may not have landed
}

_BY_KIND = {
    ("decision", "recorded"): DECISION_RECORDED,
    ("decision", "skipped"): DECISION_SKIPPED,
    ("decision", "failed"): DECISION_FAILED,
    ("lesson", "recorded"): LESSON_RECORDED,
    ("lesson", "skipped"): LESSON_SKIPPED,
    ("lesson", "failed"): LESSON_FAILED,
}


def event_for(kind, outcome):
    """The event name for one detection outcome, or None if there is none.

    None rather than a guess: emitting an unmapped outcome under some default
    name would report a signal nobody defined, and silently mis-categorise the
    very failures this surface exists to expose. Absence is caught by the test
    that walks the module's own outcome strings.
    """
    category = OUTCOMES.get(outcome)
    return _BY_KIND.get((kind, category)) if category else None
