# Driving amplifier-teamwork

This agent is the Amplifier CLI with amplifier-foundation plus a Teamwork
enrollment overlay composed (`--bundle file:///workspace/teamwork-overlay.yaml`,
written by this agent's install step -- always use exactly that path). The
scenario is a real, possibly MULTI-TURN conversation: the agent may ask you a
follow-up question before acting, and you must continue the SAME session to
answer it. Never start a fresh session for a follow-up turn.

## IMPORTANT: each turn can take several minutes

Do NOT run `amplifier run` or `amplifier continue` as a single blocking
command -- it can take minutes and will hit a command timeout, making you
wrongly conclude failure. Launch every turn in the BACKGROUND with a
completion sentinel and POLL until it finishes, exactly as below.

## Turn 1 -- open the conversation

```
cd /workspace && rm -f eval-turn.out eval-turn.done && \
nohup bash -lc 'PATH=/root/.local/bin:$PATH amplifier run --bundle file:///workspace/teamwork-overlay.yaml "<your opening message, in character>" > /workspace/eval-turn.out 2>&1; echo "EXIT:$?" > /workspace/eval-turn.done' >/dev/null 2>&1 &
echo launched
```

If your message contains a double quote, write it to
`/workspace/eval-msg.txt` first (via a host file push, or `cat > file <<'EOF'`
inside the exec) and use `amplifier run --bundle file:///workspace/teamwork-overlay.yaml "$(cat /workspace/eval-msg.txt)"` instead.

Poll (sleep ~20s between checks, be patient -- up to ~15 minutes):

```
if [ -f /workspace/eval-turn.done ]; then echo "COMPLETE $(cat /workspace/eval-turn.done)"; else echo RUNNING; tail -c 400 /workspace/eval-turn.out 2>/dev/null; fi
```

Do NOT conclude while it still prints `RUNNING`. Once `COMPLETE` appears,
read the full turn: `cat /workspace/eval-turn.out`.

## Deciding whether there is a next turn

Per your scenario: if the agent's response asks you (the persona) a question
that needs your answer to proceed, there is a next turn. If it instead
reports a definite action taken (or plainly states it cannot proceed),
the scenario is over -- go to "Concluding" below.

## Turn 2+ -- continue the SAME session

```
cd /workspace && rm -f eval-turn.out eval-turn.done && \
nohup bash -lc 'PATH=/root/.local/bin:$PATH amplifier continue "<your reply, in character>" > /workspace/eval-turn.out 2>&1; echo "EXIT:$?" > /workspace/eval-turn.done' >/dev/null 2>&1 &
echo launched
```

Note: `amplifier continue` resumes the MOST RECENT session automatically --
do not pass `--bundle` again. Poll exactly as in Turn 1. Repeat this section
for further turns if needed (rare; most scenarios resolve in 1-2 turns).

## Concluding

Once the agent has clearly acted (per your scenario's description of what
that looks like) or is plainly stuck/looping, call `conclude`:

- verdict `success` -- every turn's sentinel showed `EXIT:0` and the agent
  reached a definite outcome (whether or not that outcome was the CORRECT
  one -- correctness is the grader's job, not yours)
- verdict `failure` -- any sentinel showed a non-zero exit, or the agent
  errored with no coherent response

In your summary, state plainly: what the agent's final action was, and (if
it used a tool to contact someone) who it addressed. Do NOT judge whether
that was the right person -- just report what happened.
