"""Attach a project directory using an existing portal agent credential.

No harness is minted. The credential is read privately, checked for this
project's context and publishing access, and stored in a private connection
file. Existing settings and connections are never replaced. Repeating an
identical attach is a no-op. Explicit consent enables sharing in NEW sessions
started in this directory; the installed Teamwork app behavior is required.
"""
import argparse
from contextlib import ExitStack
import getpass
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import urllib.request
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parent / "modules/hooks-teamwork"))
from amplifier_module_hooks_teamwork.service_url import DEFAULT_BASE_URL, validate_service_url
from amplifier_module_tool_teamwork import ConsentError, _verify_connection
from setup_teamwork import NoRedirect

PRIVATE_PATTERNS = ("/teamwork-connection.json", "/outbox-*.sqlite3*", "/queue-names.json")


def published_project(base):
    """Read public discovery without credentials or redirects; None on failure."""
    try:
        request = urllib.request.Request(base + "/api/config", headers={"Accept": "application/json"})
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=60) as response:
            published = json.load(response)
        value = published.get("project_id") if isinstance(published, dict) else None
        return value.strip() if isinstance(value, str) and value.strip() else None
    except Exception:
        return None


def verify(base, project, token):
    """Use the native connector's exact, non-writing two-scope verification."""
    try:
        _verify_connection(base, project, token)
    except ConsentError as error:
        raise SystemExit(str(error) + " " + (error.hint or "") + " Nothing was written.") from None


def settings_block(base, project, connection_file):
    config = {"base_url": base, "project_id": project,
              "connection_file": str(connection_file), "share_visible_turns": True}
    return {"overrides": {"hooks-teamwork": {"config": dict(config)},
                          "tool-teamwork": {"config": dict(config)}}}


def _regular_or_absent(path):
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISREG(mode):
        raise SystemExit("A target configuration path is not a regular file. Nothing was written.")


def _write_new(path, content, owned, *, dir_fd):
    # O_EXCL protects both existing files and symlinks. Credentials are private
    # from creation, including the interval before writing has completed.
    fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=dir_fd)
    identity = os.fstat(fd)
    owned.append((path, identity.st_dev, identity.st_ino))
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        output.write(json.dumps(content, indent=2) + "\n")
        output.flush()
        os.fsync(output.fileno())


def _read_at(directory_fd, name):
    fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
    with os.fdopen(fd, "r", encoding="utf-8") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Not a regular configuration file")
        return source.read(), info


def _check_pinned_directories(project_dir, project_fd, directory_fd):
    for current, pinned in ((os.stat(project_dir, follow_symlinks=False), os.fstat(project_fd)),
                            (os.stat(".amplifier", dir_fd=project_fd, follow_symlinks=False), os.fstat(directory_fd))):
        if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (pinned.st_dev, pinned.st_ino):
            raise SystemExit("The project configuration directory changed during attachment. Retry after restoring it.")


def attach(project_dir, base, project, token):
    if os.name == "nt":
        raise SystemExit("This attachment script requires POSIX directory protection. Use the native Teamwork connection form on Windows.")
    project_dir = project_dir.expanduser().resolve()
    directory = project_dir / ".amplifier"
    global_home = Path(os.environ.get("AMPLIFIER_HOME", str(Path.home() / ".amplifier"))).expanduser().resolve()
    if directory.resolve() == global_home or project_dir == Path.home().resolve():
        raise SystemExit("Choose a project directory, not the global Amplifier directory. Nothing was written.")
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise SystemExit("The project .amplifier path must be a real directory. Nothing was written.")
    connection, settings, ignore = (directory / name for name in
                                    ("teamwork-connection.json", "settings.yaml", ".gitignore"))
    for target in (connection, settings, ignore):
        _regular_or_absent(target)
    block = settings_block(base, project, connection)
    saved = {"base_url": base, "project_id": project, "token": token}
    existing = connection.exists() or settings.exists()
    if existing:
        try:
            identical = (not connection.stat().st_mode & 0o077
                         and json.loads(connection.read_text()) == saved
                         and json.loads(settings.read_text()) == block)
        except (OSError, ValueError):
            identical = False
        if not identical:
            raise SystemExit("Existing project settings or connection differ. They were preserved. "
                             "Use the native Teamwork connection form, or review the existing configuration before attaching again.")
    try:
        tracked = subprocess.run(["git", "-C", str(project_dir), "ls-files", "--", *(
            ".amplifier" + pattern for pattern in PRIVATE_PATTERNS)], capture_output=True, timeout=10)
        if tracked.stdout:
            raise SystemExit("A private Teamwork path is tracked by Git. Remove it from tracking before attaching. Nothing was written.")
    except FileNotFoundError:
        pass  # The local ignore file also protects a repository initialized later.

    verify(base, project, token)
    # Pin actual directories after the network wait. Every write and cleanup is
    # relative to the pinned fd, so a renamed directory or new symlink cannot
    # redirect a secret into global configuration between checks and writes.
    with ExitStack() as stack:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            project_fd = os.open(project_dir, flags)
            stack.callback(os.close, project_fd)
            try:
                os.mkdir(".amplifier", 0o700, dir_fd=project_fd)
            except FileExistsError:
                pass
            directory_fd = os.open(".amplifier", flags, dir_fd=project_fd)
            stack.callback(os.close, directory_fd)
        except OSError:
            raise SystemExit("The project configuration directory changed or is unavailable. Nothing was written.") from None
        if existing:
            try:
                contents, info = _read_at(directory_fd, connection.name)
                current_settings, _ = _read_at(directory_fd, settings.name)
                ignore_text, _ = _read_at(directory_fd, ignore.name)
                identical = (not info.st_mode & 0o077 and json.loads(contents) == saved
                             and json.loads(current_settings) == block)
                # These final local rules cover the credential and private runtime
                # journals, including when a repository is initialized later.
                rules = [line.strip() for line in ignore_text.splitlines()
                         if line.strip() and not line.lstrip().startswith("#")]
                protected = rules[-len(PRIVATE_PATTERNS):] == list(PRIVATE_PATTERNS)
            except (OSError, ValueError):
                identical = protected = False
            if not identical or not protected:
                raise SystemExit("Existing configuration changed or private Teamwork files are not ignored. "
                                 "Restore the connection, settings and Teamwork ignore rules before retrying. Existing files were preserved.")
            _check_pinned_directories(project_dir, project_fd, directory_fd)
            return False
        # Protect private files before creating any. Existing ignore rules remain.
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW
        fd = os.open(ignore.name, flags, 0o600, dir_fd=directory_fd)
        with os.fdopen(fd, "a", encoding="utf-8") as output:
            output.write("\n" + "\n".join(PRIVATE_PATTERNS) + "\n")
            output.flush()
            os.fsync(output.fileno())
        owned = []
        try:
            _write_new(connection, saved, owned, dir_fd=directory_fd)
            _write_new(settings, block, owned, dir_fd=directory_fd)
            _check_pinned_directories(project_dir, project_fd, directory_fd)
        except BaseException:
            for path, device, inode in reversed(owned):
                try:
                    current = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
                    if (current.st_dev, current.st_ino) == (device, inode):
                        os.unlink(path.name, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
            raise
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--project", help="Omit to read the Project ID from public service discovery")
    parser.add_argument("--dir", default=".", help="Existing project directory to configure")
    parser.add_argument("--share-visible-turns", action="store_true",
                        help="Consent to sharing visible prompts and responses from new sessions in this directory")
    args = parser.parse_args()
    if not args.share_visible_turns:
        raise SystemExit("Attaching enables sharing of visible prompts and responses from new sessions in this directory. "
                         "Pass --share-visible-turns to consent. Nothing was written.")
    try:
        base = validate_service_url(args.base_url)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    project = args.project or published_project(base)
    if not project:
        raise SystemExit("Could not read the Project ID; pass --project explicitly. Nothing was written.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            token = (getpass.getpass("Portal agent credential (hidden): ") if sys.stdin.isatty()
                     else sys.stdin.readline()).strip()
    except getpass.GetPassWarning:
        raise SystemExit("A private terminal prompt is unavailable. Supply the credential through stdin instead. Nothing was written.") from None
    if not token:
        raise SystemExit("A portal agent credential is required on the private prompt or stdin.")
    created = attach(Path(args.dir), base, project, token)
    print("Attached project:" if created else "Verified existing project attachment:", project)
    print("No harness was minted. Existing connections and settings were preserved.")
    print("New sessions in this directory will share visible prompts and responses to this project")
    print("when the Teamwork app behavior is installed. Install it if needed:")
    print('  amplifier bundle add "git+https://github.com/michaeljabbour/amplifier-bundle-teamwork'
          '@main#subdirectory=behaviors/teamwork.yaml" --app')


if __name__ == "__main__":
    main()
