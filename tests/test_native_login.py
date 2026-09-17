"""Native login failure boundaries; synthetic credentials, no external requests."""
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import re
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules/hooks-teamwork"))
import amplifier_module_tool_teamwork as native
from amplifier_module_tool_teamwork.page import form_page

BASE = "https://team.example.invalid"
FORM = {"project": "selected", "name": "Fixture", "code": "fixture-personal-code", "consent": "yes"}


def failure(status, code, message):
    return urllib.error.HTTPError(BASE, status, "fixture", {}, io.BytesIO(json.dumps(
        {"error": {"code": code, "message": message, "request_id": "fixture"}}).encode()))


def probe(request):
    if request.full_url.endswith("/context"):
        raise failure(404, "session_not_found", "Session not found")
    if request.full_url.endswith("/publish"):
        raise failure(422, "invalid_request", "Supply 1–20 operations")
    raise AssertionError("Unexpected request")


class Response:
    def __init__(self, body, headers=None):
        self.body, self.headers = json.dumps(body).encode(), headers or {}
    def read(self): return self.body
    def __enter__(self): return self
    def __exit__(self, *args): pass


class ConnectionValidationTests(unittest.TestCase):
    def saved(self, home):
        folder = Path(home) / native.sha(BASE + "\0selected")
        folder.mkdir(mode=0o700)
        path = folder / "connection.json"
        path.write_text(json.dumps({"base_url": BASE, "project_id": "selected", "token": "old-fixture-token"}))
        path.chmod(0o600)
        return path

    def test_saved_connection_checks_both_scopes_without_minting(self):
        calls = []
        class Opener:
            def open(self, request, **kwargs):
                calls.append(request)
                return probe(request)
        with tempfile.TemporaryDirectory() as home:
            path = self.saved(home); before = path.read_bytes()
            with patch("urllib.request.build_opener", return_value=Opener()):
                result = native.connect({"project": "selected", "consent": "yes"}, BASE, Path(home))
            self.assertEqual(result, (path, "selected"))
            self.assertEqual(path.read_bytes(), before)
        self.assertEqual([r.full_url.rsplit("/", 1)[1] for r in calls], ["context", "publish"])
        self.assertEqual([json.loads(r.data) for r in calls], [{}, {"operations": []}])
        self.assertTrue(all(r.get_header("Authorization") == "Bearer old-fixture-token" for r in calls))

    def test_saved_rejection_never_overwrites_or_mints(self):
        for status,code in [(401,"unauthorized"),(403,"invalid_request"),(404,"not_found"),(503,"unavailable")]:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as home:
                path = self.saved(home); before = path.read_bytes(); calls = []
                class Opener:
                    def open(self, request, **kwargs):
                        calls.append(request.full_url)
                        raise failure(status, code, "private-service-detail")
                with patch("urllib.request.build_opener", return_value=Opener()):
                    with self.assertRaises(native.ConsentError) as error:
                        native.connect({"project":"selected","consent":"yes"}, BASE, Path(home))
                self.assertNotIn("private-service-detail", str(error.exception))
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(calls, [BASE + "/api/v1/projects/selected/context"])

    def test_new_login_fields_never_silently_reuse_existing_identity(self):
        with tempfile.TemporaryDirectory() as home:
            path=self.saved(home); before=path.read_bytes()
            with patch("urllib.request.build_opener", side_effect=AssertionError("New identity must not reuse cached credential")):
                with self.assertRaises(native.ConsentError) as error:
                    native.connect(FORM,BASE,Path(home))
            self.assertIn("not used",str(error.exception))
            self.assertEqual(path.read_bytes(),before)

    def test_connection_appearing_between_checks_cannot_replace_entered_identity(self):
        with tempfile.TemporaryDirectory() as home:
            real_directory=native._safe_directory
            calls=[]
            def directory(*args):
                result=real_directory(*args)
                calls.append(result)
                if len(calls)==2:
                    path=result/'connection.json'
                    path.write_text(json.dumps({'base_url':BASE,'project_id':'selected','token':'other-person-token'}))
                    path.chmod(0o600)
                return result
            with patch.object(native,'_safe_directory',side_effect=directory), \
                    patch('urllib.request.build_opener',side_effect=AssertionError('Must not reuse other identity')):
                with self.assertRaises(native.ConsentError) as error:
                    native.connect(FORM,BASE,Path(home))
            self.assertIn('not used',str(error.exception))
            self.assertEqual(json.loads((calls[-1]/'connection.json').read_text())['token'],'other-person-token')

    def test_portal_verifies_both_scopes_and_never_accepts_unknown_success(self):
        for scenario in ("good", "missing_read", "missing_write", "wrong_project", "unrelated_422", "malformed_404", "unexpected_200", "timeout"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as home:
                calls = []
                class Opener:
                    def open(self, request, **kwargs):
                        calls.append(request.full_url)
                        if scenario == "missing_read": raise failure(403, "invalid_request", "Context scope required")
                        if scenario == "missing_write" and request.full_url.endswith("/publish"):
                            raise failure(403, "invalid_request", "Publishing scope required")
                        if scenario == "wrong_project": raise failure(404, "not_found", "Project not found")
                        if scenario == "unrelated_422": raise failure(422, "invalid_request", "Unrelated validation")
                        if scenario == "malformed_404":
                            raise urllib.error.HTTPError(BASE, 404, "fixture", {}, io.BytesIO(b'{"error":[]}'))
                        if scenario == "unexpected_200": return Response({"ok": True})
                        if scenario == "timeout": raise TimeoutError("private transport detail")
                        return probe(request)
                with patch("urllib.request.build_opener", return_value=Opener()):
                    form = {"project": "selected", "credential": "portal-fixture-token", "consent": "yes"}
                    if scenario == "good":
                        path, _ = native.connect(form, BASE, Path(home))
                        self.assertTrue(path.exists())
                        self.assertEqual(len(calls), 2)
                    else:
                        with self.assertRaises(native.ConsentError) as error:
                            native.connect(form, BASE, Path(home))
                        self.assertNotIn("private", str(error.exception))
                        self.assertEqual(list(Path(home).rglob("connection.json")), [])

    def test_no_probe_before_consent(self):
        with tempfile.TemporaryDirectory() as home:
            self.saved(home)
            with patch("urllib.request.build_opener", side_effect=AssertionError("Network before consent")):
                with self.assertRaises(native.ConsentError):
                    native.connect({"project": "selected"}, BASE, Path(home))


class MemberFailureTests(unittest.TestCase):
    def test_ambiguous_mint_failure_requires_portal_review_before_retry(self):
        for kind in ('timeout','malformed','server_error'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as home:
                calls=[]
                class Opener:
                    def open(self,request,**kwargs):
                        calls.append(request.full_url)
                        if request.full_url.endswith('/api/login'):
                            return Response({}, {'Set-Cookie':'fixture=cookie'})
                        if kind=='timeout':raise TimeoutError('private timeout detail')
                        if kind=='malformed':raise ValueError('private malformed response detail')
                        raise failure(503,'unavailable','private server detail')
                with patch('urllib.request.build_opener',return_value=Opener()):
                    with self.assertRaises(native.ConsentError) as raised:
                        native.connect(FORM,BASE,Path(home))
                self.assertIn('outcome is unknown',str(raised.exception))
                self.assertIn('before retrying',raised.exception.hint)
                self.assertNotIn('private',str(raised.exception))
                self.assertEqual(len(calls),2)
                self.assertEqual(list(Path(home).rglob('connection.json')),[])

    def test_member_errors_are_stage_specific_and_do_not_echo_service_text(self):
        cases = [("login",401,"code","not accepted"), ("harnesses",404,"project","access"),
                 ("harnesses",403,"project","enroll"), ("login",429,None,"shortly"),
                 ("login",503,None,"unavailable")]
        for endpoint,status,field,text in cases:
            with self.subTest(endpoint=endpoint,status=status), tempfile.TemporaryDirectory() as home:
                class Opener:
                    def open(self, request, **kwargs):
                        if request.full_url.endswith("/api/" + endpoint):
                            raise failure(status, "fixture", "do-not-echo fixture-personal-code")
                        return Response({}, {"Set-Cookie": "fixture=cookie; HttpOnly"})
                with patch("urllib.request.build_opener", return_value=Opener()):
                    with self.assertRaises(native.ConsentError) as raised:
                        native.connect(FORM, BASE, Path(home))
                self.assertEqual(raised.exception.field, field)
                self.assertIn(text, str(raised.exception).lower())
                self.assertNotIn("do-not-echo", str(raised.exception))
                self.assertNotIn(FORM["code"], str(raised.exception))
                self.assertEqual(list(Path(home).rglob("connection.json")), [])


class SSOFailureTests(unittest.TestCase):
    def test_every_postmint_save_failure_revokes_new_credential(self):
        for case in ("directory", "reservation", "missing_project"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as home:
                calls=[]
                def send(base, endpoint, body, bearer, extra_headers=None):
                    calls.append(endpoint)
                    return ({"id":"new-fixture-id","token":"new-fixture-token","projects":[] if case=="missing_project" else ["selected"]}, {})
                patches=[patch.object(native,"_fetch_service_config",return_value={"api_app_id":"fixture"}),
                         patch.object(native.entra,"access_token",return_value="fixture-bearer"),
                         patch.object(native,"_http_bearer",side_effect=send)]
                if case=="directory": patches.append(patch.object(native,"_safe_directory",side_effect=native.ConsentError("Fixture folder error")))
                if case=="reservation": patches.append(patch.object(native.os,"open",side_effect=PermissionError("Fixture reservation failure")))
                from contextlib import ExitStack
                with ExitStack() as stack:
                    for context in patches:stack.enter_context(context)
                    with self.assertRaises((native.ConsentError, PermissionError)):
                        native._mint_sso({"consent":"yes"},BASE,Path(home),"","https://github.com/fixture/repo")
                self.assertEqual(calls,["/api/harnesses","/api/harnesses/revoke"])

    def test_actual_sso_ambiguity_envelope_shows_only_bounded_project_ids(self):
        payload={"error":{"code":"choose_project","choices":[{"id":"project-a","private":"secret-detail"},{"id":"project-b"}]}}
        with tempfile.TemporaryDirectory() as home, patch.object(native,"_fetch_service_config",return_value={"api_app_id":"fixture"}), \
             patch.object(native.entra,"access_token",return_value="fixture-bearer"), \
             patch.object(native,"_http_bearer",side_effect=native._MintError(409,payload)):
            with self.assertRaises(native.ConsentError) as raised:
                native._mint_sso({"consent":"yes"},BASE,Path(home),"","https://github.com/fixture/repo")
        self.assertIn("project-a",raised.exception.hint)
        self.assertIn("project-b",raised.exception.hint)
        self.assertNotIn("secret-detail",raised.exception.hint)


class LoginFormTests(unittest.TestCase):
    def test_personal_code_fields_precede_explicit_advanced_portal_credential(self):
        page=form_page("nonce","csrf","style",BASE,15)
        self.assertLess(page.index('id="name"'),page.index('id="code"'))
        self.assertLess(page.index('id="code"'),page.index('id="credential"'))
        self.assertIn("Personal access code",page)
        self.assertIn("<details",page)
        self.assertIn("not your personal access code",page)


class PendingCancellationTests(unittest.TestCase):
    def test_cancel_stop_and_expiry_compensate_only_newly_owned_credentials(self):
        for cause, mode in [('cancel','member'),('stop','member'),('expiry','member'),
                            ('cancel','sso'),('cancel','saved'),('cancel','portal')]:
            with self.subTest(cause=cause,mode=mode), tempfile.TemporaryDirectory() as directory:
                home=Path(directory);stop=threading.Event()
                opened=threading.Event();entered=threading.Event();release=threading.Event()
                seen={};calls=[]
                if mode=='saved':
                    folder=home/native.sha(BASE+'\0selected');folder.mkdir(mode=0o700)
                    saved=folder/'connection.json'
                    saved.write_text(json.dumps({'base_url':BASE,'project_id':'selected','token':'existing-token'}));saved.chmod(0o600)
                    before=saved.read_bytes()
                def service(base,endpoint,body=None,headers=None,origin=None):
                    calls.append(endpoint)
                    if endpoint=='/api/config':return {'api_app_id':'fixture-app'},{}
                    if endpoint=='/api/login':return {},{'Set-Cookie':'fixture=cookie'}
                    if endpoint=='/api/harnesses' or endpoint.endswith('/context'):
                        entered.set()
                        if not release.wait(3):raise TimeoutError('Fixture barrier timeout')
                        if endpoint.endswith('/context'):raise failure(404,'session_not_found','Session not found')
                        return {'id':'new-fixture-id','token':'new-fixture-token','projects':['selected']},{}
                    if endpoint=='/api/harnesses/revoke':return {'revoked':True},{}
                    raise AssertionError('Unexpected service endpoint')
                def notify(url,*args):seen['url']=url;opened.set()
                def run():
                    try:seen['result']=native.private_browser_connect(BASE,home,stop,timeout=.2 if cause=='expiry' else 5,notify=notify)
                    except native.ConsentAborted as error:seen['error']=str(error)
                def post(body):
                    request=urllib.request.Request(seen['url'],data=urllib.parse.urlencode(body).encode(),headers={
                        'Content-Type':'application/x-www-form-urlencoded','Origin':'http://'+urllib.parse.urlsplit(seen['url']).netloc})
                    try:
                        with urllib.request.urlopen(request,timeout=3) as response:return response.read().decode()
                    except urllib.error.HTTPError as error:
                        try:return error.read().decode()
                        finally:error.close()
                with patch.object(native,'_http',side_effect=service), \
                        patch.object(native.entra,'available',return_value=mode=='sso'), \
                        patch.object(native.entra,'access_token',return_value='fixture-entra-token'), \
                        patch.object(native.webbrowser,'open',return_value=True):
                    worker=threading.Thread(target=run,daemon=True);worker.start()
                    self.assertTrue(opened.wait(2))
                    with urllib.request.urlopen(seen['url'],timeout=2) as response:page=response.read().decode()
                    csrf=re.search(r'name="csrf" value="([^"]+)"',page).group(1)
                    form={'csrf':csrf,'project':'selected','consent':'yes'}
                    if mode=='member':form.update(name='Fixture',code='fixture-code')
                    if mode=='portal':form['credential']='portal-token'
                    pending=threading.Thread(target=lambda:seen.update(submit_response=post(form)),daemon=True);pending.start()
                    try:
                        self.assertTrue(entered.wait(2))
                        if cause=='cancel':
                            cancel_page=post({'csrf':csrf,'action':'cancel'})
                            self.assertNotIn('no credential was created',cancel_page)
                            self.assertIn('may need cleanup',cancel_page)
                        elif cause=='stop':stop.set()
                        worker.join(1.5)
                        self.assertFalse(worker.is_alive(),'Cancellation must not wait on the blocked service')
                        self.assertIn('cleanup',seen.get('error',''))
                    finally:
                        release.set();pending.join(3);worker.join(3)
                    self.assertFalse(pending.is_alive())
                    self.assertNotIn('result',seen)
                self.assertEqual(calls.count('/api/harnesses/revoke'),int(mode in ('member','sso')))
                if mode=='saved':self.assertEqual(saved.read_bytes(),before)
                else:self.assertEqual(list(home.rglob('connection.json')),[])

    def test_completion_wins_over_late_cancel(self):
        stop=threading.Event()
        attempt=native.ConsentAttempt(stop)
        attempt.complete()
        stop.set()
        self.assertFalse(attempt.cancel())
        attempt.complete()


if __name__ == "__main__":unittest.main()
