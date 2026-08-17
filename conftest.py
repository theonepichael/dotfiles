import atexit
import io
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

_REAL_HOME = Path(os.path.expanduser("~")).resolve()
_SANDBOX_HOME = tempfile.mkdtemp(prefix="dotfiles-pytest-home-")
os.environ["HOME"] = _SANDBOX_HOME
atexit.register(shutil.rmtree, _SANDBOX_HOME, ignore_errors=True)

import pytest  # noqa: E402 — HOME must be redirected before any other import

_ORIGINAL_POPEN_INIT = subprocess.Popen.__init__
_ORIGINAL_OPEN = io.open
_ORIGINAL_OS_OPEN = os.open
_WRITE_MODE_CHARS = frozenset("wax+")
_GUARDED_HOME_SUBDIRS = (_REAL_HOME / ".claude", _REAL_HOME / ".config")


def _is_write_mode(mode: str) -> bool:
    return any(c in _WRITE_MODE_CHARS for c in mode)


def _resolve_if_path(target: object) -> Path | None:
    if not isinstance(target, (str, bytes, os.PathLike)):
        return None
    return Path(os.fsdecode(target)).expanduser().resolve()


def _is_guarded_target(resolved: Path) -> bool:
    return any(resolved.is_relative_to(d) for d in _GUARDED_HOME_SUBDIRS)


def _deny_production_path(resolved: Path, marker: str) -> None:
    raise RuntimeError(
        f"blocked real filesystem access to {resolved} under the real "
        f"HOME during a test — mark the test with @pytest.mark.{marker} "
        "to allow it"
    )


@pytest.fixture(autouse=True)
def guard_real_subprocess(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed = request.node.get_closest_marker("allow_real_subprocess") is not None

    def guarded_init(self: subprocess.Popen, *args: object, **kwargs: object) -> None:
        if not allowed:
            raise RuntimeError(
                "blocked a real subprocess.Popen/run/call/check_output call "
                "during a test — mark the test with "
                "@pytest.mark.allow_real_subprocess to allow it"
            )
        _ORIGINAL_POPEN_INIT(self, *args, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "__init__", guarded_init)


@pytest.fixture(autouse=True)
def guard_production_paths(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed = request.node.get_closest_marker("allow_production_paths") is not None
    if allowed:
        monkeypatch.setenv("HOME", str(_REAL_HOME))

    def guarded_open(
        file: object, mode: str = "r", *args: object, **kwargs: object
    ) -> object:
        resolved = _resolve_if_path(file)
        if (
            resolved is not None
            and _is_write_mode(mode)
            and _is_guarded_target(resolved)
            and not allowed
        ):
            _deny_production_path(resolved, "allow_production_paths")
        return _ORIGINAL_OPEN(file, mode, *args, **kwargs)

    def make_guarded(original: object, *, path_args: tuple[int, ...] = (0,)) -> object:
        def guarded(*args: object, **kwargs: object) -> object:
            if not allowed:
                for i in path_args:
                    if i >= len(args):
                        continue
                    resolved = _resolve_if_path(args[i])
                    if resolved is not None and _is_guarded_target(resolved):
                        _deny_production_path(resolved, "allow_production_paths")
            return original(*args, **kwargs)

        return guarded

    def guarded_os_open(
        path: object, flags: int, *args: object, **kwargs: object
    ) -> object:
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT
        if not allowed and (flags & write_flags):
            resolved = _resolve_if_path(path)
            if resolved is not None and _is_guarded_target(resolved):
                _deny_production_path(resolved, "allow_production_paths")
        return _ORIGINAL_OS_OPEN(path, flags, *args, **kwargs)

    monkeypatch.setattr("builtins.open", guarded_open)
    monkeypatch.setattr("io.open", guarded_open)
    monkeypatch.setattr(Path, "write_text", make_guarded(Path.write_text))
    monkeypatch.setattr(Path, "write_bytes", make_guarded(Path.write_bytes))
    monkeypatch.setattr(Path, "unlink", make_guarded(Path.unlink))
    monkeypatch.setattr(Path, "rmdir", make_guarded(Path.rmdir))
    monkeypatch.setattr(Path, "mkdir", make_guarded(Path.mkdir))
    monkeypatch.setattr(Path, "rename", make_guarded(Path.rename, path_args=(0, 1)))
    monkeypatch.setattr(Path, "replace", make_guarded(Path.replace, path_args=(0, 1)))
    monkeypatch.setattr(os, "open", guarded_os_open)
    monkeypatch.setattr(os, "remove", make_guarded(os.remove))
    monkeypatch.setattr(os, "unlink", make_guarded(os.unlink))
    monkeypatch.setattr(os, "rmdir", make_guarded(os.rmdir))
    monkeypatch.setattr(os, "mkdir", make_guarded(os.mkdir))
    monkeypatch.setattr(os, "makedirs", make_guarded(os.makedirs))
    monkeypatch.setattr(os, "rename", make_guarded(os.rename, path_args=(0, 1)))
    monkeypatch.setattr(os, "replace", make_guarded(os.replace, path_args=(0, 1)))
    monkeypatch.setattr(shutil, "rmtree", make_guarded(shutil.rmtree))
