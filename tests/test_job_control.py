"""Job control on the running app (#433): the server's endpoints, the CLI, the shell.

The server tests talk real HTTP to a real listener on an ephemeral loopback
port. The GUI hook behind it is a fake for those, and the real window further
down, where the request comes from another thread as it does in the app.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import socket
import subprocess
import sys
import threading
from dataclasses import replace

import pytest

from benchhud import app, cli_job
from benchhud.browser_sources import BrowserSourceServer, TopicHub
from benchhud.browser_sources.control import (
    FIELD_LIMITS,
    MAX_DEVICE_CHARS,
    STATUSES,
    JobControl,
    clean_fields,
    read_control_file,
    remove_control_file,
    write_control_file,
)
from benchhud.browser_sources.payloads import JOB_FIELDS, job_reply, jobs_payload
from benchhud.config import Config
from benchhud.jobs.store import Job, JobStatus, JobStore
from benchhud.shell.main_window import MainWindow
from conftest import SUBPROCESS_TIMEOUT_S, subprocess_env

#: What must never leave the app, whatever the command.
PII = ("Jane Doe", "F2LXK0ABCDEF", "died after a swim")


def _job(job_id: int, device: str, **fields) -> Job:
    base = Job(
        id=job_id,
        device=device,
        serial="F2LXK0ABCDEF",
        customer="Jane Doe",
        symptoms="Jane says it died after a swim",
        status="open",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    return replace(base, **fields)


class FakeBench:
    """Stands in for the GUI: jobs carrying PII, and which one is selected."""

    def __init__(self) -> None:
        self.jobs = [_job(1, "iPhone 12", external_ref="26-0001", board_id="820-00239")]
        self.selected: int | None = None
        self.slow = False

    def list_jobs(self) -> dict:
        return jobs_payload(reversed(self.jobs), self.selected)

    def create_job(self, fields: dict) -> dict:
        if self.slow:
            raise TimeoutError
        job = replace(_job(len(self.jobs) + 1, ""), **fields)
        self.jobs.append(job)
        self.selected = job.id
        return job_reply(job, self.selected)

    def select_job(self, job_id: int) -> dict | None:
        for job in self.jobs:
            if job.id == job_id:
                self.selected = job_id
                return job_reply(job, self.selected)
        return None

    def update_job(self, job_id: int, fields: dict) -> dict | None:
        for index, job in enumerate(self.jobs):
            if job.id == job_id:
                self.jobs[index] = replace(job, **fields)
                return job_reply(self.jobs[index], self.selected)
        return None


@pytest.fixture
def served(tmp_path):
    bench = FakeBench()
    control = JobControl(bench.list_jobs, bench.create_job, bench.select_job, bench.update_job)
    server = BrowserSourceServer(TopicHub(), 0, jobs=control)
    server.start()
    control_file = tmp_path / "control.json"
    write_control_file(control_file, server.port, control.token)
    yield bench, server, control, control_file
    server.stop()


def _request(server, method, path, *, token=None, body=None, headers=None):
    headers = dict(headers or {})
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    payload = json.dumps(body).encode() if body is not None else None
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    conn.request(method, path, body=payload, headers=headers)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, data


# --- the control file --------------------------------------------------------


def test_the_control_file_round_trips(tmp_path) -> None:
    path = tmp_path / "sub" / "control.json"
    write_control_file(path, 8780, "s3cret")
    assert read_control_file(path) == (8780, "s3cret")


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_the_control_file_is_owner_only_even_over_a_loose_one(tmp_path) -> None:
    path = tmp_path / "control.json"
    path.write_text("{}")
    path.chmod(0o644)
    write_control_file(path, 8780, "s3cret")
    assert path.stat().st_mode & 0o777 == 0o600


def test_a_missing_or_broken_control_file_reads_as_none(tmp_path) -> None:
    path = tmp_path / "control.json"
    assert read_control_file(path) is None
    path.write_text("not json")
    assert read_control_file(path) is None
    path.write_text('{"port": 8780}')
    assert read_control_file(path) is None


def test_only_this_runs_control_file_is_removed(tmp_path) -> None:
    path = tmp_path / "control.json"
    write_control_file(path, 8780, "theirs")
    remove_control_file(path, "mine")
    assert path.exists()
    remove_control_file(path, "theirs")
    assert not path.exists()


def test_the_token_stays_out_of_the_repr() -> None:
    control = JobControl(dict, dict, lambda job_id: None, lambda job_id, fields: None)
    assert control.token not in repr(control)
    assert (
        control.token
        != JobControl(dict, dict, lambda job_id: None, lambda job_id, fields: None).token
    )


# --- the server --------------------------------------------------------------


def test_list_returns_the_whitelisted_fields_and_the_selection(served) -> None:
    bench, server, control, _ = served
    bench.selected = 1
    status, data = _request(server, "GET", "/control/jobs", token=control.token)
    assert status == 200
    reply = json.loads(data)
    assert reply["selected"] == 1
    assert [set(job) for job in reply["jobs"]] == [set(JOB_FIELDS)]
    assert reply["jobs"][0]["external_ref"] == "26-0001"


def test_new_creates_and_selects(served) -> None:
    bench, server, control, _ = served
    status, data = _request(
        server, "POST", "/control/jobs", token=control.token, body={"device": "  Switch OLED "}
    )
    assert status == 200
    reply = json.loads(data)
    assert reply["job"]["device"] == "Switch OLED"
    assert reply["selected"] == reply["job"]["id"] == bench.selected == 2


def test_select_picks_a_job_by_id(served) -> None:
    bench, server, control, _ = served
    status, data = _request(
        server, "POST", "/control/jobs/select", token=control.token, body={"id": 1}
    )
    assert status == 200
    assert json.loads(data)["selected"] == 1
    assert bench.selected == 1


def test_new_takes_the_other_fields(served) -> None:
    bench, server, control, _ = served
    body = {
        "device": "Grandstream HT801",
        "board_id": " HT801_MB V1.1A ",
        "serial": "SN-NEW-1",
        "customer": "Sam Roe",
        "symptoms": "no dial tone",
        "status": "in_progress",
    }
    status, data = _request(server, "POST", "/control/jobs", token=control.token, body=body)
    assert status == 200
    reply = json.loads(data)
    assert reply["job"]["board_id"] == "HT801_MB V1.1A"
    assert reply["job"]["status"] == "in_progress"
    made = bench.jobs[-1]
    assert (made.serial, made.customer, made.symptoms) == ("SN-NEW-1", "Sam Roe", "no dial tone")


def test_set_changes_only_the_fields_named(served) -> None:
    bench, server, control, _ = served
    before = bench.jobs[0]
    status, data = _request(
        server,
        "POST",
        "/control/jobs/set",
        token=control.token,
        body={"id": 1, "board_id": "820-01700", "status": "waiting"},
    )
    assert status == 200
    reply = json.loads(data)
    assert reply["job"]["board_id"] == "820-01700" and reply["job"]["status"] == "waiting"
    assert reply["selected"] is None  # a set doesn't move the selection
    assert bench.jobs[0] == replace(before, board_id="820-01700", status="waiting")


def test_set_can_clear_a_field(served) -> None:
    bench, server, control, _ = served
    ok = _request(
        server, "POST", "/control/jobs/set", token=control.token, body={"id": 1, "serial": ""}
    )
    assert ok[0] == 200 and bench.jobs[0].serial == ""


def test_set_of_an_unknown_job_is_404(served) -> None:
    _, server, control, _ = served
    status, data = _request(
        server, "POST", "/control/jobs/set", token=control.token, body={"id": 99, "serial": "x"}
    )
    assert status == 404
    assert json.loads(data) == {"error": "no job #99"}


def test_a_bad_status_is_refused_with_the_list(served) -> None:
    _, server, control, _ = served
    status, data = _request(
        server, "POST", "/control/jobs/set", token=control.token, body={"id": 1, "status": "fixed"}
    )
    assert status == 400
    assert json.loads(data) == {
        "error": "status must be one of open, in_progress, waiting, done, scrapped"
    }


def test_the_statuses_are_the_stores() -> None:
    assert list(STATUSES) == [status.value for status in JobStatus]


def test_an_error_names_the_field_and_never_the_value() -> None:
    secret = "Jane Doe " * 40
    with pytest.raises(ValueError, match="customer is over 200 characters") as caught:
        clean_fields({"customer": secret})
    assert "Jane" not in str(caught.value)
    with pytest.raises(ValueError, match="status must be one of") as caught:
        clean_fields({"status": "Jane Doe"})
    assert "Jane" not in str(caught.value)


def test_written_pii_never_comes_back(served) -> None:
    """Customer, serial and symptoms go in; no reply, and no later list, carries them."""
    _, server, control, _ = served
    written = {"customer": "Sam Roe", "serial": "SN-WRITTEN-9", "symptoms": "fell in a lake"}
    replies = [
        _request(
            server, "POST", "/control/jobs", token=control.token, body={"device": "PS5", **written}
        ),
        _request(
            server, "POST", "/control/jobs/set", token=control.token, body={"id": 1, **written}
        ),
        _request(server, "GET", "/control/jobs", token=control.token),
        _request(server, "POST", "/control/jobs/select", token=control.token, body={"id": 1}),
    ]
    for status, data in replies:
        assert status == 200
        text = data.decode()
        assert not [value for value in (*written.values(), *PII) if value in text]
        assert not [name for name in written if name in text]
    listed = json.loads(replies[2][1])
    assert [set(job) for job in listed["jobs"]] == [set(JOB_FIELDS)] * 2


def test_select_of_an_unknown_job_is_404(served) -> None:
    bench, server, control, _ = served
    status, data = _request(
        server, "POST", "/control/jobs/select", token=control.token, body={"id": 99}
    )
    assert status == 404
    assert json.loads(data) == {"error": "no job #99"}
    assert bench.selected is None


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/control/jobs", {}),
        ("/control/jobs", {"device": "   "}),
        ("/control/jobs", {"device": 7}),
        ("/control/jobs", {"device": "x" * (MAX_DEVICE_CHARS + 1)}),
        ("/control/jobs", ["iPhone"]),
        ("/control/jobs/select", {}),
        ("/control/jobs/select", {"id": "1"}),
        ("/control/jobs/select", {"id": True}),
        ("/control/jobs", {"device": "PS5", "status": "fixed"}),
        ("/control/jobs", {"device": "PS5", "external_ref": "26-0001"}),
        ("/control/jobs", {"device": "PS5", "customer": 7}),
        ("/control/jobs", {"device": "PS5", "symptoms": "x" * (FIELD_LIMITS["symptoms"] + 1)}),
        ("/control/jobs", {"board_id": "820-00239"}),
        ("/control/jobs/set", {"board_id": "820-00239"}),
        ("/control/jobs/set", {"id": "1", "board_id": "820-00239"}),
        ("/control/jobs/set", {"id": 1}),
        ("/control/jobs/set", {"id": 1, "device": "  "}),
        ("/control/jobs/set", {"id": 1, "status": "fixed"}),
        ("/control/jobs/set", {"id": 1, "id2": "x"}),
        ("/control/jobs/set", {"id": 1, "created_at": "2020-01-01"}),
        ("/control/jobs/set", {"id": 1, "serial": "x" * (FIELD_LIMITS["serial"] + 1)}),
        ("/control/jobs/set", {"id": 1, "customer": "x" * (FIELD_LIMITS["customer"] + 1)}),
        ("/control/jobs/set", {"id": 1, "board_id": "x" * (FIELD_LIMITS["board_id"] + 1)}),
    ],
)
def test_a_bad_body_is_400_and_changes_nothing(served, path, body) -> None:
    bench, server, control, _ = served
    status, _data = _request(server, "POST", path, token=control.token, body=body)
    assert status == 400
    assert len(bench.jobs) == 1 and bench.selected is None
    assert bench.jobs[0] == FakeBench().jobs[0]


def test_a_gui_that_does_not_answer_is_503(served) -> None:
    bench, server, control, _ = served
    bench.slow = True
    status, _data = _request(
        server, "POST", "/control/jobs", token=control.token, body={"device": "PS5"}
    )
    assert status == 503


def test_only_the_three_commands_exist(served) -> None:
    _, server, control, _ = served
    assert _request(server, "GET", "/control/outputs", token=control.token)[0] == 404
    assert _request(server, "POST", "/control/scene", token=control.token, body={})[0] == 404
    assert _request(server, "GET", "/control/jobs/select", token=control.token)[0] == 404
    assert _request(server, "GET", "/control/jobs/set", token=control.token)[0] == 404
    assert _request(server, "POST", "/control/jobs/delete", token=control.token, body={})[0] == 404
    assert _request(server, "POST", "/control/jobs/notes", token=control.token, body={})[0] == 404


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer nope"},
        {"Authorization": "Bearer "},
        {"Authorization": "Basic TOKEN"},
    ],
)
def test_every_command_needs_the_token(served, headers) -> None:
    bench, server, control, _ = served
    headers = {k: v.replace("TOKEN", control.token) for k, v in headers.items()}
    assert _request(server, "GET", "/control/jobs", headers=headers)[0] == 403
    new = _request(server, "POST", "/control/jobs", body={"device": "PS5"}, headers=headers)
    select = _request(server, "POST", "/control/jobs/select", body={"id": 1}, headers=headers)
    change = _request(
        server, "POST", "/control/jobs/set", body={"id": 1, "status": "done"}, headers=headers
    )
    assert new[0] == select[0] == change[0] == 403
    assert len(bench.jobs) == 1 and bench.selected is None
    assert bench.jobs[0].status == "open"


def test_the_token_is_not_taken_from_the_url(served) -> None:
    """A token in the URL would land in the request log, so it isn't accepted there."""
    bench, server, control, _ = served
    path = f"/control/jobs?token={control.token}"
    assert _request(server, "GET", path)[0] == 403
    assert _request(server, "POST", path, body={"device": "PS5"})[0] == 403
    assert len(bench.jobs) == 1


def test_a_foreign_host_header_is_refused(served) -> None:
    bench, server, control, _ = served
    rebound = {"Host": f"evil.example:{server.port}"}
    assert _request(server, "GET", "/control/jobs", token=control.token, headers=rebound)[0] == 403
    status, _data = _request(
        server, "POST", "/control/jobs", token=control.token, body={"device": "x"}, headers=rebound
    )
    assert status == 403
    assert len(bench.jobs) == 1


def test_a_request_from_another_origin_is_refused(served) -> None:
    """A page in a local browser can't drive it, even holding the token."""
    bench, server, control, _ = served
    cross = {"Origin": "https://evil.example"}
    assert _request(server, "GET", "/control/jobs", token=control.token, headers=cross)[0] == 403
    for path, body in (
        ("/control/jobs", {"device": "x"}),
        ("/control/jobs/select", {"id": 1}),
        ("/control/jobs/set", {"id": 1, "status": "done"}),
    ):
        status, _data = _request(
            server, "POST", path, token=control.token, body=body, headers=cross
        )
        assert status == 403
        rebound = {"Host": f"evil.example:{server.port}"}
        status, _data = _request(
            server, "POST", path, token=control.token, body=body, headers=rebound
        )
        assert status == 403
    assert len(bench.jobs) == 1 and bench.selected is None
    assert bench.jobs[0].status == "open"


def test_no_cors_headers_are_ever_sent(served) -> None:
    _, server, control, _ = served
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    conn.request("GET", "/control/jobs", headers={"Authorization": f"Bearer {control.token}"})
    response = conn.getresponse()
    response.read()
    conn.close()
    assert not [name for name, _ in response.getheaders() if name.lower().startswith("access-")]


def test_without_job_control_the_paths_are_refused() -> None:
    server = BrowserSourceServer(TopicHub(), 0)
    server.start()
    try:
        assert _request(server, "GET", "/control/jobs", token="anything")[0] == 403
        status, _data = _request(
            server, "POST", "/control/jobs", token="anything", body={"device": "x"}
        )
        assert status == 403
    finally:
        server.stop()


def test_replies_never_carry_customer_serial_or_symptoms(served) -> None:
    _, server, control, _ = served
    replies = [
        _request(server, "GET", "/control/jobs", token=control.token),
        _request(server, "POST", "/control/jobs", token=control.token, body={"device": "PS5"}),
        _request(server, "POST", "/control/jobs/select", token=control.token, body={"id": 1}),
        _request(server, "POST", "/control/jobs/select", token=control.token, body={"id": 99}),
    ]
    for _status, data in replies:
        text = data.decode()
        assert not [secret for secret in PII if secret in text]
        for field in ("customer", "serial", "symptoms"):
            assert field not in text


def test_the_token_is_never_logged(served, caplog) -> None:
    _, server, control, _ = served
    with caplog.at_level(logging.DEBUG):
        _request(server, "GET", "/control/jobs", token=control.token)
        _request(server, "POST", "/control/jobs", token=control.token, body={"device": "PS5"})
        _request(server, "GET", "/control/jobs", headers={"Authorization": "Bearer wrong"})
    assert "/control/jobs" in caplog.text  # the requests were logged
    assert control.token not in caplog.text


# --- the CLI -----------------------------------------------------------------


def test_cli_list_new_select_end_to_end(served, capsys) -> None:
    bench, _server, _control, control_file = served

    assert cli_job.main(["list"], control_file) == 0
    assert capsys.readouterr().out == "  #1  iPhone 12 — open  820-00239  26-0001\n"

    assert cli_job.main(["new", "MacBook", "Pro", "A1708"], control_file) == 0
    assert capsys.readouterr().out == "* #2  MacBook Pro A1708 — open\n"
    assert bench.selected == 2

    assert cli_job.main(["select", "1"], control_file) == 0
    assert capsys.readouterr().out.startswith("* #1  iPhone 12")
    assert bench.selected == 1

    assert cli_job.main(["list"], control_file) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("  #2") and out[1].startswith("* #1")
    assert not [secret for secret in PII if secret in "\n".join(out)]


def test_cli_new_with_fields_and_set(served, capsys) -> None:
    bench, _server, _control, control_file = served
    code = cli_job.main(
        ["new", "Grandstream", "HT801", "--board-id", "HT801_MB V1.1A", "--customer", "Sam Roe"],
        control_file,
    )
    assert code == 0
    assert capsys.readouterr().out == "* #2  Grandstream HT801 — open  HT801_MB V1.1A\n"
    assert bench.jobs[-1].customer == "Sam Roe"

    code = cli_job.main(
        ["set", "2", "--status", "waiting", "--serial", "SN-WRITTEN-9", "--symptoms", "no tone"],
        control_file,
    )
    assert code == 0
    out = capsys.readouterr().out
    assert out == "* #2  Grandstream HT801 — waiting  HT801_MB V1.1A\n"
    job = bench.jobs[-1]
    assert (job.status, job.serial, job.symptoms) == ("waiting", "SN-WRITTEN-9", "no tone")
    assert job.customer == "Sam Roe"  # not named, not touched

    assert cli_job.main(["set", "2", "--device", "HT801 v2", "--serial", ""], control_file) == 0
    assert capsys.readouterr().out.startswith("* #2  HT801 v2 — waiting")
    assert bench.jobs[-1].serial == ""

    assert cli_job.main(["list"], control_file) == 0
    listing = capsys.readouterr().out
    assert not [word for word in ("Sam Roe", "SN-WRITTEN-9", "no tone", *PII) if word in listing]


def test_cli_rejects_a_status_the_store_does_not_know(served, capsys) -> None:
    bench, _server, _control, control_file = served
    with pytest.raises(SystemExit) as stopped:
        cli_job.main(["set", "1", "--status", "fixed"], control_file)
    assert stopped.value.code == 2
    err = capsys.readouterr().err
    assert all(status in err for status in STATUSES)
    assert bench.jobs[0].status == "open"


def test_cli_set_with_nothing_to_set_is_a_usage_error(served, capsys) -> None:
    _, _server, _control, control_file = served
    with pytest.raises(SystemExit) as stopped:
        cli_job.main(["set", "1"], control_file)
    assert stopped.value.code == 2
    assert "nothing to set" in capsys.readouterr().err


def test_cli_set_of_an_unknown_job_fails_and_says_so(served, capsys) -> None:
    _, _server, _control, control_file = served
    assert cli_job.main(["set", "99", "--status", "done"], control_file) == 1
    assert "no job #99" in capsys.readouterr().err


def test_cli_json_prints_the_reply(served, capsys) -> None:
    _, _server, _control, control_file = served
    assert cli_job.main(["list", "--json"], control_file) == 0
    reply = json.loads(capsys.readouterr().out)
    assert reply["selected"] is None and reply["jobs"][0]["id"] == 1


def test_cli_select_of_an_unknown_job_fails_and_says_so(served, capsys) -> None:
    _, _server, _control, control_file = served
    assert cli_job.main(["select", "99"], control_file) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no job #99" in captured.err


def test_cli_never_prints_the_token(served, capsys) -> None:
    _, _server, control, control_file = served
    cli_job.main(["list"], control_file)
    cli_job.main(["select", "99"], control_file)
    captured = capsys.readouterr()
    assert control.token not in captured.out + captured.err


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.mark.parametrize(
    "command", [["list"], ["new", "PS5"], ["select", "1"], ["set", "1", "--status", "done"]]
)
def test_cli_says_when_the_app_is_not_running(tmp_path, capsys, command) -> None:
    assert cli_job.main(command, tmp_path / "control.json") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "benchhud isn't running" in captured.err
    assert list(tmp_path.iterdir()) == []  # and nothing was written, jobs.db included


def test_cli_treats_a_file_left_by_a_crash_as_not_running(tmp_path, capsys) -> None:
    control_file = tmp_path / "control.json"
    write_control_file(control_file, _free_port(), "stale")
    assert cli_job.main(["new", "PS5"], control_file) == 1
    assert "benchhud isn't running" in capsys.readouterr().err


def test_cli_treats_another_server_on_the_port_as_not_running(served, tmp_path, capsys) -> None:
    _, server, _control, _ = served
    control_file = tmp_path / "control.json"
    write_control_file(control_file, server.port, "from-an-older-run")
    assert cli_job.main(["list"], control_file) == 1
    assert "benchhud isn't running" in capsys.readouterr().err


def test_job_is_a_terminal_command() -> None:
    assert "job" in app.COMMANDS


def test_the_command_runs_without_qt_and_without_the_app(tmp_path) -> None:
    """`benchhud job list` with no app: exit 1, a reason, no Qt, no jobs.db."""
    code = (
        "import sys, runpy\n"
        "sys.argv = ['benchhud', 'job', 'list']\n"
        "try:\n    runpy.run_module('benchhud', run_name='__main__')\n"
        "except SystemExit as e:\n    code = e.code\n"
        "assert not any(m.startswith('PySide6') for m in sys.modules), 'Qt was imported'\n"
        "sys.exit(code)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        env=subprocess_env(BENCHHUD_CONFIG=str(tmp_path / "config.toml")),
        timeout=SUBPROCESS_TIMEOUT_S,
    )
    assert done.returncode == 1, done.stderr
    assert "benchhud isn't running" in done.stderr
    assert list(tmp_path.iterdir()) == []


# --- the shell ---------------------------------------------------------------


@pytest.fixture
def store():
    s = JobStore(":memory:")
    yield s
    s.close()


@pytest.fixture
def window(qtbot, store, tmp_path):
    config = Config()
    config.browser_sources.enabled = True
    config.browser_sources.port = 0
    w = MainWindow(config, job_store=store, control_file=tmp_path / "control.json")
    qtbot.addWidget(w)
    w.start_browser_sources()
    yield w
    w.stop_browser_sources()


def _from_another_thread(qtbot, call):
    """Run ``call`` off the GUI thread, as the CLI's request arrives, and pump Qt meanwhile."""
    result: list = []
    thread = threading.Thread(target=lambda: result.append(call()), daemon=True)
    thread.start()
    qtbot.waitUntil(lambda: bool(result), timeout=10_000)
    return result[0]


def test_the_window_writes_the_control_file_and_removes_it(window, tmp_path) -> None:
    control_file = tmp_path / "control.json"
    port, token = read_control_file(control_file)
    assert port == window.browser_sources.port
    assert token == window.browser_sources.jobs.token
    if os.name != "nt":
        assert control_file.stat().st_mode & 0o777 == 0o600
    window.stop_browser_sources()
    assert not control_file.exists()


def test_each_start_makes_a_new_token(window, tmp_path) -> None:
    first = read_control_file(tmp_path / "control.json")[1]
    window.restart_browser_sources()
    second = read_control_file(tmp_path / "control.json")[1]
    assert first != second


def test_without_a_control_file_job_control_is_off(qtbot, store) -> None:
    config = Config()
    config.browser_sources.enabled = True
    config.browser_sources.port = 0
    w = MainWindow(config, job_store=store)
    qtbot.addWidget(w)
    w.start_browser_sources()
    try:
        assert w.browser_sources.jobs is None
    finally:
        w.stop_browser_sources()


def test_cli_new_lands_in_the_panel_and_on_the_job_card(qtbot, window, store, tmp_path) -> None:
    window.jobs_panel.new_device_edit.setText("half-typed")
    code = _from_another_thread(
        qtbot, lambda: cli_job.main(["new", "Switch", "OLED"], tmp_path / "control.json")
    )
    assert code == 0
    job = store.list_jobs()[0]
    assert job.device == "Switch OLED"
    assert window.jobs_panel.selected_job_id() == job.id
    assert window.jobs_panel.job_list.count() == 1
    assert window.jobs_panel.new_device_edit.text() == "half-typed"
    _, card = window._browser_hub.latest("job")
    assert card["job"]["device"] == "Switch OLED"


def test_cli_select_moves_the_panel_and_the_job_card(qtbot, window, store, tmp_path) -> None:
    older = store.create_job("MacBook Pro", customer="Jane Doe", board_id="820-00239")
    window.jobs_panel.refresh_jobs()
    # Made after the list was loaded, as an import in another window would be.
    newer = store.create_job("PS5", customer="Jane Doe", serial="F2LXK0ABCDEF")
    control_file = tmp_path / "control.json"

    for job in (older, newer):
        code = _from_another_thread(
            qtbot, lambda job=job: cli_job.main(["select", str(job.id)], control_file)
        )
        assert code == 0
        assert window.jobs_panel.selected_job_id() == job.id
        _, card = window._browser_hub.latest("job")
        assert card["job"]["id"] == job.id


def test_cli_list_from_the_window_is_pii_free(qtbot, window, store, tmp_path, capsys) -> None:
    job = store.create_job(
        "iPhone 12",
        serial="F2LXK0ABCDEF",
        customer="Jane Doe",
        symptoms="Jane says it died after a swim",
    )
    window.jobs_panel.refresh_jobs()
    window.jobs_panel.select_job(job.id)
    control_file = tmp_path / "control.json"
    code = _from_another_thread(qtbot, lambda: cli_job.main(["list", "--json"], control_file))
    assert code == 0
    out = capsys.readouterr().out
    reply = json.loads(out)
    assert reply["selected"] == job.id
    assert set(reply["jobs"][0]) == set(JOB_FIELDS)
    assert not [secret for secret in PII if secret in out]


def test_the_work_runs_on_the_gui_thread(qtbot, window) -> None:
    ran_on = _from_another_thread(qtbot, lambda: window._on_gui_thread(threading.get_ident))
    assert ran_on == threading.get_ident()


def test_a_busy_gui_times_out_and_the_job_is_not_made_late(
    qtbot, window, store, monkeypatch
) -> None:
    monkeypatch.setattr("benchhud.shell.main_window.JOB_COMMAND_TIMEOUT_S", 0.05)
    outcome: list = []

    def call() -> None:
        try:
            window.browser_sources.jobs.create_job({"device": "PS5"})
        except TimeoutError:
            outcome.append("timeout")

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    thread.join(5)  # the GUI thread is busy right here, so the command can't run
    assert outcome == ["timeout"]
    qtbot.wait(50)  # now the queued call is delivered, and finds itself cancelled
    assert store.list_jobs() == []


def test_new_job_button_still_reads_and_clears_the_box(qtbot, window, store) -> None:
    window.jobs_panel.new_device_edit.setText("  Game Boy  ")
    window.jobs_panel.create_job()
    assert store.list_jobs()[0].device == "Game Boy"
    assert window.jobs_panel.new_device_edit.text() == ""
    assert window.jobs_panel.create_job_named("   ") is None


# --- the shell: fields (#436) --------------------------------------------------


def test_cli_new_with_fields_reaches_the_store_and_the_card(qtbot, window, store, tmp_path) -> None:
    command = ["new", "Grandstream", "HT801", "--board-id", "HT801_MB V1.1A"]
    command += ["--serial", "SN-NEW-1", "--customer", "Sam Roe", "--symptoms", "no dial tone"]
    command += ["--status", "in_progress"]
    code = _from_another_thread(qtbot, lambda: cli_job.main(command, tmp_path / "control.json"))
    assert code == 0
    job = store.list_jobs()[0]
    assert (job.device, job.board_id, job.status) == (
        "Grandstream HT801",
        "HT801_MB V1.1A",
        "in_progress",
    )
    assert (job.serial, job.customer, job.symptoms) == ("SN-NEW-1", "Sam Roe", "no dial tone")
    panel = window.jobs_panel
    assert panel.selected_job_id() == job.id
    assert panel.customer_edit.text() == "Sam Roe"  # the dock shows it; the card doesn't
    _, card = window._browser_hub.latest("job")
    assert card["job"]["board_id"] == "HT801_MB V1.1A"
    assert "Sam Roe" not in json.dumps(card)


def test_cli_set_on_the_open_job_reloads_the_form(qtbot, window, store, tmp_path, capsys) -> None:
    job = store.create_job("Grandstream HT801", customer="Jane Doe", serial="F2LXK0ABCDEF")
    panel = window.jobs_panel
    panel.refresh_jobs()
    panel.select_job(job.id)
    stamp = store.get_job(job.id).updated_at
    command = ["set", str(job.id), "--board-id", "HT801_MB V1.1A", "--status", "waiting"]
    command += ["--customer", "Sam Roe", "--symptoms", "no dial tone", "--device", "HT801"]
    code = _from_another_thread(qtbot, lambda: cli_job.main(command, tmp_path / "control.json"))
    assert code == 0
    out = capsys.readouterr().out
    assert out == f"* #{job.id}  HT801 — waiting  HT801_MB V1.1A\n"

    assert panel.selected_job_id() == job.id
    assert panel.job_list.currentItem().text() == f"#{job.id}  HT801 — waiting"
    assert panel.device_edit.text() == "HT801"
    assert panel.customer_edit.text() == "Sam Roe"
    assert panel.symptoms_edit.toPlainText() == "no dial tone"
    assert panel.status_combo.currentText() == "waiting"
    _, card = window._browser_hub.latest("job")
    assert card["job"]["board_id"] == "HT801_MB V1.1A" and card["job"]["status"] == "waiting"
    assert store.get_job(job.id).updated_at >= stamp

    # A Save from the form now writes what the command set, not what was there before.
    panel.save_current()
    saved = store.get_job(job.id)
    assert (saved.device, saved.customer, saved.status) == ("HT801", "Sam Roe", "waiting")
    assert saved.serial == "F2LXK0ABCDEF" and saved.board_id == "HT801_MB V1.1A"


def test_cli_set_on_another_job_leaves_the_form_and_selection_alone(
    qtbot, window, store, tmp_path
) -> None:
    other = store.create_job("PS5")
    open_job = store.create_job("Switch OLED")
    panel = window.jobs_panel
    panel.refresh_jobs()
    panel.select_job(open_job.id)
    panel.symptoms_edit.setPlainText("half-typed")
    code = _from_another_thread(
        qtbot,
        lambda: cli_job.main(["set", str(other.id), "--status", "done"], tmp_path / "control.json"),
    )
    assert code == 0
    assert store.get_job(other.id).status == "done"
    assert panel.selected_job_id() == open_job.id
    assert panel.symptoms_edit.toPlainText() == "half-typed"
    rows = [panel.job_list.item(row).text() for row in range(panel.job_list.count())]
    assert f"#{other.id}  PS5 — done" in rows
    _, card = window._browser_hub.latest("job")
    assert card["job"]["id"] == open_job.id


def test_cli_set_on_a_job_the_list_has_not_seen(qtbot, window, store, tmp_path) -> None:
    shown = store.create_job("Switch OLED")
    panel = window.jobs_panel
    panel.refresh_jobs()
    panel.select_job(shown.id)
    late = store.create_job("PS5")  # made after the list was loaded
    code = _from_another_thread(
        qtbot,
        lambda: cli_job.main(
            ["set", str(late.id), "--board-id", "EDM-010"], tmp_path / "control.json"
        ),
    )
    assert code == 0
    assert store.get_job(late.id).board_id == "EDM-010"
    assert panel.job_list.count() == 2
    assert panel.selected_job_id() == shown.id


def test_cli_set_from_the_window_never_returns_pii(qtbot, window, store, tmp_path, capsys) -> None:
    job = store.create_job("iPhone 12", customer="Jane Doe", serial="F2LXK0ABCDEF")
    window.jobs_panel.refresh_jobs()
    command = ["set", str(job.id), "--json", "--customer", "Sam Roe"]
    command += ["--serial", "SN-WRITTEN-9", "--symptoms", "fell in a lake"]
    code = _from_another_thread(qtbot, lambda: cli_job.main(command, tmp_path / "control.json"))
    assert code == 0
    out = capsys.readouterr().out
    assert set(json.loads(out)["job"]) == set(JOB_FIELDS)
    assert not [w for w in ("Sam Roe", "SN-WRITTEN-9", "fell in a lake", *PII) if w in out]
    assert store.get_job(job.id).customer == "Sam Roe"


def test_update_job_fields_of_a_missing_job_is_none(qtbot, window) -> None:
    assert window.jobs_panel.update_job_fields(99, status="done") is None
