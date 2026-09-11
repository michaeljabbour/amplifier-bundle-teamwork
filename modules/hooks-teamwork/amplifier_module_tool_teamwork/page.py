"""Private consent-form rendering. Loopback-only pages; never renders a member code."""
import html

# CSP stays `default-src 'none'`; a per-response nonce is the only way styles load.
STYLE = """
:root{color-scheme:light dark;--bg:#f4f6f9;--card:#fff;--ink:#12161c;--muted:#5b6472;
--line:#e3e7ee;--accent:#2f5bd7;--on-accent:#fff;--ok:#0f7b4f;--ok-bg:#e9f7f0;
--bad:#a8271f;--bad-bg:#fdeceb;--warn:#7a5300;--warn-bg:#fff5e2}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--card:#161b22;--ink:#e6edf3;
--muted:#9aa4b2;--line:#293039;--accent:#6f9bff;--on-accent:#0b1020;--ok:#56d39b;
--ok-bg:#0f2620;--bad:#ff9c93;--bad-bg:#2c1614;--warn:#e9c47f;--warn-bg:#2a2214}}
*{box-sizing:border-box}
body{margin:0;padding:40px 16px;background:var(--bg);color:var(--ink);
font:15px/1.55 ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
main{max-width:580px;margin:0 auto;background:var(--card);border:1px solid var(--line);
border-radius:14px;padding:30px 32px 26px;box-shadow:0 1px 2px rgba(0,0,0,.04),0 10px 30px rgba(0,0,0,.06)}
.brand{display:flex;align-items:center;gap:9px;font-size:12.5px;font-weight:600;
letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:18px}
.brand svg{display:block}
h1{font-size:23px;line-height:1.25;margin:0 0 8px;letter-spacing:-.01em}
.lede{margin:0 0 20px;color:var(--muted)}
dl.facts{margin:0 0 22px;border:1px solid var(--line);border-radius:10px;overflow:hidden}
dl.facts>div{display:flex;gap:14px;padding:9px 13px;border-top:1px solid var(--line);font-size:13.5px}
dl.facts>div:first-child{border-top:0}
dl.facts dt{margin:0;width:104px;flex:none;color:var(--muted)}
dl.facts dd{margin:0;font-weight:550;word-break:break-word}
.note{border-radius:10px;padding:12px 14px;margin:0 0 20px;font-size:13.5px;
border:1px solid;display:flex;gap:10px;align-items:flex-start}
.note svg{flex:none;margin-top:2px}
.note.bad{color:var(--bad);background:var(--bad-bg);border-color:currentColor}
.note.warn{color:var(--warn);background:var(--warn-bg);border-color:currentColor}
.note.ok{color:var(--ok);background:var(--ok-bg);border-color:currentColor}
.note b{display:block;margin-bottom:2px}
.note p{margin:0;color:inherit}
.field{margin:0 0 17px}
.field label{display:block;font-weight:600;font-size:13.5px;margin-bottom:5px}
.field .opt{font-weight:400;color:var(--muted)}
.field input[type=text],.field input[type=password]{width:100%;padding:9px 11px;font:inherit;
color:var(--ink);background:var(--card);border:1px solid var(--line);border-radius:8px}
.field input:focus{outline:2px solid var(--accent);outline-offset:1px;border-color:var(--accent)}
.field input[aria-invalid=true]{border-color:var(--bad);outline-color:var(--bad)}
.help{margin:5px 0 0;font-size:12.5px;color:var(--muted)}
.consent{display:flex;gap:10px;align-items:flex-start;padding:13px;margin:22px 0 20px;
border:1px solid var(--line);border-radius:10px;font-size:13.5px}
.consent input{margin:2px 0 0;width:16px;height:16px;flex:none;accent-color:var(--accent)}
.consent[data-invalid=true]{border-color:var(--bad)}
.actions{display:flex;gap:10px;align-items:center}
button{font:inherit;font-weight:600;border-radius:9px;padding:10px 16px;cursor:pointer;border:1px solid transparent}
button.primary{background:var(--accent);color:var(--on-accent);flex:1}
button.ghost{background:transparent;color:var(--muted);border-color:var(--line)}
.foot{margin:22px 0 0;padding-top:16px;border-top:1px solid var(--line);
font-size:12.5px;color:var(--muted)}
.foot code{font-size:12px;word-break:break-all}
"""


def _icon(kind):
    """Inline SVG only; CSP forbids external or data-URI images."""
    body = {
        "mark": '<path d="M3 8.5 8 3l5 5.5V15H3z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>',
        "bad": '<circle cx="8" cy="8" r="6.6" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M8 4.7v4.1M8 11.1v.9" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
        "warn": '<path d="M8 2.3 15 14H1z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M8 6.4v3.2M8 11.6v.8" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
        "ok": '<circle cx="8" cy="8" r="6.6" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="m5 8.2 2.1 2.2L11 6.1" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>',
    }[kind]
    return '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">' + body + "</svg>"


def _shell(nonce, title, body):
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>" + html.escape(title) + "</title>"
        '<style nonce="' + nonce + '">' + STYLE + "</style>"
        '<body><main><div class="brand">' + _icon("mark") + "Amplifier Teamwork</div>" + body + "</main>"
    )


def _note(kind, heading, detail):
    return (
        '<div class="note ' + kind + '" role="alert">' + _icon(kind)
        + "<div><b>" + html.escape(heading) + "</b><p>" + html.escape(detail) + "</p></div></div>"
    )


def form_page(nonce, csrf, style_nonce, service, minutes, values=None, error=None, field=None, hint=None,
              sso=False, repository=None):
    """Consent form. `values` echoes only the project/name the user typed here; never the code.

    `sso=True` shows the Microsoft sign-in row and relabels the member-code
    fields as a fallback. `repository` (when a git remote was detected) shows
    the exact URL that will be sent, before consent, and makes the project
    field optional once SSO can resolve it from that repository instead.
    """
    values = values or {}
    banner = _note("bad", error, hint or "Correct the highlighted field and submit again. Nothing has been shared yet.") if error else ""
    project_optional = sso and repository
    code_label = "member-code fallback only" if sso else "first enrollment only"

    def text(name, label, optional, help_text, kind="text", extra=""):
        invalid = ' aria-invalid="true"' if field == name else ""
        value = ' value="' + html.escape(values.get(name, ""), quote=True) + '"' if kind == "text" else ""
        return (
            '<div class="field"><label for="' + name + '">' + html.escape(label)
            + ('<span class="opt"> — ' + html.escape(optional) + "</span>" if optional else "")
            + '</label><input id="' + name + '" name="' + name + '" type="' + kind + '"'
            + value + invalid + extra + '><p class="help">' + html.escape(help_text) + "</p></div>"
        )

    facts = (
        "<div><dt>Service</dt><dd>" + html.escape(service) + "</dd></div>"
        "<div><dt>Shares</dt><dd>Your visible prompts and Amplifier's final responses</dd></div>"
        "<div><dt>Receives</dt><dd>Bounded, attributed project context</dd></div>"
        "<div><dt>Grants</dt><dd>context:read, session:write and shared:write, for this project only</dd></div>"
    )
    if sso:
        facts += "<div><dt>Sign-in</dt><dd>Your Microsoft account (az login)</dd></div>"
    if repository:
        facts += "<div><dt>Repository</dt><dd>" + html.escape(repository) + "</dd></div>"

    return _shell(style_nonce, "Connect Teamwork", (
        "<h1>Connect this session to a project</h1>"
        '<p class="lede">Sharing is off until you submit this form. It applies to this one session '
        "and starts with your next prompt.</p>"
        '<dl class="facts">' + facts + "</dl>"
        + banner
        + '<form method="post" action="/' + nonce + '">'
        + '<input type="hidden" name="csrf" value="' + html.escape(csrf, quote=True) + '">'
        + text("project", "Project ID", "optional — detected from the repository" if project_optional else "required",
               "Leave blank to use the project linked to this repository." if project_optional
               else "Exactly as it appears in Teamwork, for example design/review.",
               extra=('' if project_optional else ' required')
               + ' maxlength="200" autocomplete="off" autocapitalize="off" spellcheck="false" autofocus')
        + text("credential", "Credential from the portal", "optional",
               "Paste a credential minted from Account menu \u2192 Harnesses & agents. "
               "Takes precedence over the fields below.",
               kind="password", extra=' autocomplete="off"')
        + text("name", "Name or email", code_label,
               "Leave blank if this project is already enrolled on this machine.",
               extra=' autocomplete="username"')
        + text("code", "Private member code", code_label,
               "Sent straight to the service and never written to disk.",
               kind="password", extra=' autocomplete="off"')
        + '<div class="consent"' + (' data-invalid="true"' if field == "consent" else "")
        + '><input id="consent" name="consent" type="checkbox" value="yes" required>'
        '<label for="consent">Share subsequent visible prompts and final responses in this session with this '
        "project, publish shared knowledge and work requests to it on my behalf, and receive its bounded "
        "context.</label></div>"
        '<div class="actions"><button class="primary" type="submit">Connect and enable sharing</button>'
        '<button class="ghost" type="submit" name="action" value="cancel" formnovalidate>Cancel</button></div>'
        "</form>"
        + _note("warn", "Never paste your member code into the Amplifier chat.",
                "This local form is the only place it belongs. It goes directly to the service and is not saved.")
        + '<p class="foot">This form is served from your own machine on the loopback interface and is '
        "reachable only from this browser. The window closes after about " + html.escape(str(minutes))
        + " minutes with no activity; re-run the connect tool to reopen it.</p>"
    ))


def result_page(style_nonce, kind, title, status, detail, footer=""):
    return _shell(style_nonce, title, (
        "<h1>" + html.escape(title) + "</h1>"
        + _note(kind, status, detail)
        + ('<p class="foot">' + html.escape(footer) + "</p>" if footer else "")
    ))
