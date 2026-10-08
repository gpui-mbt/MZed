"""Private-runner candidate: own and reap one foreground Mozc child."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Protocol


class ChildProcess(Protocol):
    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...


def verified_reaped_status(record: str, expected_pid: int) -> int | None:
    """Return a child status only for a complete matching reaped event."""
    try:
        event = json.loads(record)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(event, dict):
        return None
    pid = event.get("pid")
    returncode = event.get("returncode")
    if (
        event.get("event") != "reaped"
        or type(pid) is not int
        or pid != expected_pid
        or event.get("wait_completed") is not True
        or type(returncode) is not int
    ):
        return None
    return returncode


def summarize_lifecycle(
    started_event: object,
    reaped_event: object,
    supervisor_wait_status: object,
) -> dict[str, object]:
    """Build Mozc summary fields from protocol records and the wait result.

    Bash owns coprocess bookkeeping and may unset the generated ``*_PID``
    variable when it reaps that named coprocess. The typed started/reaped
    records remain the lifecycle identity source; the shell's actual wait
    status is passed separately.
    """
    summary: dict[str, object] = {
        "child_started": "not-observed",
        "supervisor_pid": None,
        "supervisor_parent_pid": None,
        "child_pid": None,
        "child_parent_pid": None,
        "child_start_time": None,
        "child_wait_verified": False,
        "child_returncode": "missing-reaped-event",
        "supervisor_waited": False,
        "supervisor_exit_status": "not-observed",
    }
    if not isinstance(started_event, dict):
        return summary
    pid = started_event.get("pid")
    parent_pid = started_event.get("parent_pid")
    start_time = started_event.get("start_time")
    supervisor_pid = started_event.get("supervisor_pid")
    supervisor_parent_pid = started_event.get("supervisor_parent_pid")
    if not (
        started_event.get("event") == "started"
        and type(pid) is int
        and pid > 0
        and type(supervisor_pid) is int
        and supervisor_pid > 0
        and type(parent_pid) is int
        and parent_pid == supervisor_pid
        and type(start_time) is int
        and start_time > 0
        and type(supervisor_parent_pid) is int
        and supervisor_parent_pid > 0
    ):
        return summary

    summary.update(
        {
            "child_started": True,
            "supervisor_pid": supervisor_pid,
            "supervisor_parent_pid": supervisor_parent_pid,
            "child_pid": pid,
            "child_parent_pid": parent_pid,
            "child_start_time": start_time,
        }
    )
    reaped_record = json.dumps(reaped_event) if isinstance(reaped_event, dict) else ""
    returncode = verified_reaped_status(reaped_record, pid)
    if returncode is not None:
        summary["child_wait_verified"] = True
        summary["child_returncode"] = returncode
    elif isinstance(reaped_event, dict):
        summary["child_returncode"] = "unverified-reaped-event"

    if type(supervisor_wait_status) is int:
        summary["supervisor_waited"] = True
        summary["supervisor_exit_status"] = supervisor_wait_status
    return summary


def _read_json_record(path: str, *, last: bool = False) -> object:
    try:
        lines = [line for line in Path(path).read_text(encoding="utf-8").splitlines() if line]
    except OSError:
        return None
    if not lines:
        return None
    try:
        return json.loads(lines[-1] if last else lines[0])
    except json.JSONDecodeError:
        return None


def summarize_files(started_path: str, reaped_path: str, wait_status: str) -> dict[str, object]:
    try:
        parsed_wait_status: object = int(wait_status)
    except (TypeError, ValueError):
        parsed_wait_status = None
    return summarize_lifecycle(
        _read_json_record(started_path),
        _read_json_record(reaped_path, last=True),
        parsed_wait_status,
    )


_SUMMARY_FIELDS = (
    "child_started",
    "supervisor_pid",
    "supervisor_parent_pid",
    "child_pid",
    "child_parent_pid",
    "child_start_time",
    "child_wait_verified",
    "child_returncode",
    "supervisor_waited",
    "supervisor_exit_status",
)


def summary_tsv(summary: dict[str, object]) -> str:
    """Render one stable summary row from the typed lifecycle result."""
    values = []
    for field in _SUMMARY_FIELDS:
        value = summary[field]
        if field in ("child_started", "child_wait_verified", "supervisor_waited"):
            if value is True:
                values.append("1")
            elif value is False:
                values.append("0")
            else:
                values.append(str(value))
        else:
            values.append("not-observed" if value is None else str(value))
    return "\t".join(_SUMMARY_FIELDS) + "\n" + "\t".join(values)


def stop_and_reap(child: ChildProcess, timeout: float = 3.0) -> int:
    """Stop a Popen-owned child and always wait it; never signal a raw PID."""
    if child.poll() is None:
        child.terminate()
    try:
        return child.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        child.kill()
        return child.wait()


def read_child_identity(pid: int) -> dict[str, int | str]:
    process_dir = Path("/proc") / str(pid)

    def sample() -> dict[str, int | str]:
        stat_text = (process_dir / "stat").read_text()
        stat_fields = stat_text[stat_text.rfind(")") + 2 :].split()
        status_text = (process_dir / "status").read_text()
        uid_line = next(line for line in status_text.splitlines() if line.startswith("Uid:"))
        return {
            "pid": pid,
            "parent_pid": int(stat_fields[1]),
            "start_time": int(stat_fields[19]),
            "uid": int(uid_line.split()[2]),
            "executable": os.readlink(process_dir / "exe"),
        }

    before = sample()
    after = sample()
    if before != after:
        raise RuntimeError("server process identity changed during startup sample")
    return after


def run(server_path: str, log_path: str) -> int:
    if not os.path.isabs(server_path) or not os.path.isfile(server_path):
        print(json.dumps({"event": "error", "reason": "server-path-invalid"}), flush=True)
        return 2
    try:
        with open(log_path, "ab", buffering=0) as log:
            child = subprocess.Popen(
                [server_path],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=os.environ.copy(),
                close_fds=True,
            )
    except OSError as error:
        print(
            json.dumps(
                {
                    "event": "error",
                    "reason": "server-spawn-failed",
                    "errno": error.errno,
                }
            ),
            flush=True,
        )
        return 3

    supervisor_status = 0
    try:
        try:
            identity = read_child_identity(child.pid)
        except (OSError, StopIteration, ValueError, IndexError):
            print(json.dumps({"event": "error", "reason": "server-identity-unreadable"}), flush=True)
            supervisor_status = 4
        else:
            if (
                child.poll() is not None
                or identity["parent_pid"] != os.getpid()
                or identity["uid"] != os.geteuid()
                or identity["executable"] != server_path
            ):
                print(json.dumps({"event": "error", "reason": "server-child-identity-mismatch"}), flush=True)
                supervisor_status = 5
            else:
                print(
                    json.dumps(
                        {
                            "event": "started",
                            "pid": child.pid,
                            "supervisor_pid": os.getpid(),
                            "supervisor_parent_pid": os.getppid(),
                            "parent_pid": identity["parent_pid"],
                            "start_time": identity["start_time"],
                        }
                    ),
                    flush=True,
                )
                for line in sys.stdin:
                    command = line.strip().lower()
                    if command == "stop":
                        break
                    if command == "status":
                        print(
                            json.dumps({"event": "status", "returncode": child.poll()}),
                            flush=True,
                        )
    except BrokenPipeError:
        # The shell may close its handshake pipe during startup. The finally
        # block still terminates and reaps the server child.
        supervisor_status = 6
    finally:
        returncode = stop_and_reap(child)
        try:
            print(
                json.dumps(
                    {
                        "event": "reaped",
                        "pid": child.pid,
                        "returncode": returncode,
                        "wait_completed": True,
                    }
                ),
                flush=True,
            )
        except BrokenPipeError:
            pass
    return supervisor_status


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--summary-tsv":
        print(summary_tsv(summarize_files(sys.argv[2], sys.argv[3], sys.argv[4])))
        raise SystemExit(0)
    if len(sys.argv) != 3:
        raise SystemExit("usage: mozc_supervisor.py SERVER_PATH SERVER_LOG")
    raise SystemExit(run(sys.argv[1], sys.argv[2]))
