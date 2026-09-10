"""Map a teamwork project onto a local work-tracker project, or refuse.

A session already binds a teamwork ``project_id`` at enrolment, and the same
project should own the queue that inbound requests land in -- so a request for
project P reaches P's queue and nowhere else, with no second mapping to keep in
step.

The two naming rules do not match. Work-tracker project names must satisfy
``^[a-z][a-z0-9_]{1,30}$`` -- lowercase, no dashes, 2-31 characters -- and a
teamwork project id need not. Normalising is therefore lossy, and lossy mappings
collide: ``design-review`` and ``design_review`` and ``Design Review`` all reduce
to the same name.

A collision here is not cosmetic. It would route one team's inbound requests into
another team's backlog, silently, which is the precise failure the join exists to
prevent. So the mapping is deterministic AND remembered: the first project to
claim a tracker name keeps it, and a second project that would take the same name
is refused loudly rather than merged into it.
"""

import json
import re
from pathlib import Path

VALID = re.compile(r"^[a-z][a-z0-9_]{1,30}$")


class QueueNameConflict(Exception):
    """Two teamwork projects would share one tracker project."""


def normalise(project_id):
    """The deterministic part: a teamwork project id to a candidate name.

    Published rather than private, because both sides of the join must apply
    exactly the same rule or the join is not a join.
    """
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("A project id is required")
    lowered = re.sub(r"[^a-z0-9]+", "_", project_id.strip().lower()).strip("_")
    if not lowered:
        raise ValueError("Project id %r has no usable characters" % project_id)
    if not lowered[0].isalpha():
        # A leading digit is legal in a teamwork id and illegal here. Prefixing
        # keeps it deterministic; dropping the digit would not be.
        lowered = "p_" + lowered
    candidate = lowered[:31]
    # Truncation can strip back to a trailing underscore, which is ugly but legal;
    # a one-character result is not, so pad rather than emit something invalid.
    candidate = candidate.rstrip("_") or "p_" + lowered[:29]
    if len(candidate) < 2:
        candidate = candidate + "_q"
    if not VALID.match(candidate):
        raise ValueError("Cannot derive a valid tracker project name from %r" % project_id)
    return candidate


def bind(project_id, registry_path):
    """The remembered part: claim the name, or refuse if another project holds it.

    Returns the tracker project name. Raises QueueNameConflict when a DIFFERENT
    teamwork project already owns it -- never quietly shares a queue.
    """
    name = normalise(project_id)
    path = Path(registry_path).expanduser()
    registry = {}
    if path.exists():
        try:
            registry = json.loads(path.read_text() or "{}")
        except ValueError:
            # A corrupt registry must not silently become an empty one: that would
            # hand away a name another project is already using.
            raise QueueNameConflict(
                "The queue-name registry at %s is unreadable; refusing to guess which "
                "project owns %r" % (path, name)
            ) from None
    held = registry.get(name)
    if held and held != project_id:
        raise QueueNameConflict(
            "Tracker project %r is already bound to teamwork project %r, so %r cannot "
            "use it. Choose an explicit queue name for one of them rather than sharing "
            "a backlog." % (name, held, project_id)
        )
    if held != project_id:
        registry[name] = project_id
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n")
        path.chmod(0o600)
    return name
