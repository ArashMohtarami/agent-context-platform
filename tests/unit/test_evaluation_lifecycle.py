"""Pure lifecycle planning/refusal contracts; no database/activation claims."""

import json

import pytest

from scripts import evaluation_lifecycle as lifecycle
from scripts import focused_runner
from scripts.evaluation_recipient import Browser, issue
from scripts.focused_process import FocusedError

INSTANCE = "a" * 32


@pytest.fixture
def evaluation(tmp_path, monkeypatch):
    root = tmp_path / "evaluations"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(lifecycle, "STATE_ROOT", root)
    value = lifecycle.Evaluation(INSTANCE)
    value.state.mkdir(mode=0o700)
    (value.state / "logs").mkdir(mode=0o700)
    (value.state / "lock").touch(mode=0o600)
    value.data = {
        "version": 1,
        "instance": INSTANCE,
        "state": str(value.state),
        "project": value.project,
        "resources": value.names,
        "files": {},
        "source": "unit-only-source",
        "origin": "https://127.0.0.1:8443",
        "stage": "prepared",
    }
    lifecycle.atomic(value.manifest, value.data)
    return value


def test_inherited_deployment_configuration_is_discarded(monkeypatch):
    for key in (
        "COMPOSE_FILE",
        "MONGODB_URI",
        "DOCKER_HOST",
        "PATH",
        "PYTHONPATH",
        "AWS_PROFILE",
    ):
        monkeypatch.setenv(key, "foreign-untrusted-input")
    assert lifecycle.clean_env() == {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
    }


@pytest.mark.parametrize("instance", ["", "live", "../" + INSTANCE, INSTANCE + ";id", "B" * 32])
def test_instance_requires_exact_evaluation_identity(instance):
    with pytest.raises(FocusedError):
        lifecycle.Evaluation(instance)


def test_symlink_and_foreign_manifest_refused_before_docker(evaluation, tmp_path):
    calls = []
    evaluation.command = lambda *a, **k: calls.append(a)
    link = evaluation.state / "foreign"
    link.symlink_to(tmp_path)
    with pytest.raises(FocusedError, match="symlink"):
        evaluation.load()
    link.unlink()
    evaluation.data["instance"] = "b" * 32
    lifecycle.atomic(evaluation.manifest, evaluation.data)
    with pytest.raises(FocusedError, match="another instance"):
        evaluation.reset()
    assert not calls


def test_resume_refuses_changed_source_but_stop_preserves_state(evaluation, monkeypatch):
    monkeypatch.setattr(lifecycle, "source_identity", lambda: "changed")
    with pytest.raises(FocusedError, match="source changed"):
        evaluation.load(resume=True)
    monkeypatch.setattr(evaluation, "owned", lambda: [])
    evaluation.stop()
    assert evaluation.state.exists()
    assert json.loads(evaluation.manifest.read_text())["stage"] == "stopped"


@pytest.mark.parametrize("kind", ["container", "network", "volume"])
def test_cleanup_checks_all_labels_before_removing_any(evaluation, monkeypatch, kind):
    calls = []

    def inspect(resource_kind, name):
        labels = {lifecycle.LABEL: INSTANCE, "com.docker.compose.project": evaluation.project}
        if resource_kind == kind:
            labels[lifecycle.LABEL] = "another-instance"
        return {"Name": name, "Id": "unit-owned-id", "Labels": labels, "Config": {"Labels": labels}}

    monkeypatch.setattr(evaluation, "inspect", inspect)
    monkeypatch.setattr(evaluation, "command", lambda *a, **k: calls.append(a))
    with pytest.raises(FocusedError, match="foreign resource"):
        evaluation.reset()
    assert not calls and evaluation.state.exists()


def test_live_volume_mount_refused_even_with_owned_labels(evaluation, monkeypatch):
    labels = {lifecycle.LABEL: INSTANCE, "com.docker.compose.project": evaluation.project}
    monkeypatch.setattr(
        evaluation,
        "inspect",
        lambda kind, name: {
            "Name": name,
            "Config": {"Labels": labels},
            "Mounts": [{"Type": "volume", "Name": "agent_context_mongodb_data"}],
        },
    )
    with pytest.raises(FocusedError, match="live volume"):
        evaluation.owned()


def test_manifest_rejects_remote_origin_and_changed_inputs(evaluation, monkeypatch):
    evaluation.data["origin"] = "https://remote.example.test:8443"
    lifecycle.atomic(evaluation.manifest, evaluation.data)
    with pytest.raises(FocusedError, match="origin"):
        evaluation.load()
    evaluation.data["origin"] = "https://127.0.0.1:8443"
    (evaluation.state / "input").write_text("original")
    evaluation.data["files"] = {"input": lifecycle.file_hash(evaluation.state / "input")}
    lifecycle.atomic(evaluation.manifest, evaluation.data)
    (evaluation.state / "input").write_text("changed")
    with pytest.raises(FocusedError, match="inputs changed"):
        evaluation.load()
    # Input drift cannot justify resuming, but does not strand label/path-owned
    # disposable resources. A foreign resource would still fail owned().
    monkeypatch.setattr(evaluation, "inspect", lambda *args: None)
    evaluation.reset()
    assert not evaluation.state.exists()


def test_stale_activation_stops_before_any_report_or_service_use(tmp_path, monkeypatch):
    folder = lifecycle.ROOT / ".private/v7-competitive-planning/phase-3/unit-only-evidence"
    # No imitation release XML: only a stale planner envelope in a temporary tree.
    monkeypatch.setattr(lifecycle, "ROOT", tmp_path)
    folder = tmp_path / ".private/v7-competitive-planning/unit-only"
    folder.mkdir(parents=True)
    (folder / "plan.json").write_text(json.dumps({"source_sha256": "stale"}))
    (folder / "activation-summary.json").write_text(
        json.dumps({"status": "passed", "source_sha256": "stale"})
    )
    with pytest.raises(FocusedError, match="stale"):
        lifecycle.activation_evidence(folder)


def test_generated_graph_has_only_owned_private_database_and_loopback_tls(evaluation):
    pins = json.loads(lifecycle.INPUTS.read_text())
    images = {
        name: "sha256:" + str(i) * 64
        for i, name in enumerate(
            (pins["mongo_image"], pins["search_image"], "api:reviewed", "console:reviewed"), 1
        )
    }
    config = lifecycle.compose_config(
        evaluation,
        {"images": images, "subnet": "172.30.4.0/28", "port": 8443},
        "api:reviewed",
        "console:reviewed",
    )
    assert not config["services"]["mongodb"].get("ports")
    assert not config["services"]["mongot"].get("ports")
    assert config["services"]["console"]["ports"] == ["127.0.0.1:8443:8443"]
    assert config["networks"]["data"]["internal"]
    import ipaddress

    networks = [
        ipaddress.ip_network(v["ipam"]["config"][0]["subnet"]) for v in config["networks"].values()
    ]
    assert len(networks) == 3 and all(
        not a.overlaps(b) for i, a in enumerate(networks) for b in networks[i + 1 :]
    )
    assert config["services"]["context-api"]["environment"]["API_AUTH_ENABLED"] == "true"
    assert "${" not in json.dumps(config)
    assert all(v["name"] in evaluation.names["volume"] for v in config["volumes"].values())


def test_one_time_delivery_refuses_existing_or_symlinked_destination(evaluation, tmp_path):
    path = tmp_path / "recipient-token"
    path.write_text("existing")
    # Fail before contacting the API; existing contents can never be overwritten.
    with pytest.raises(FileExistsError):
        issue(evaluation, None, path)
    assert path.read_text() == "existing"
    path.unlink()
    path.symlink_to(tmp_path / "missing")
    with pytest.raises(FocusedError):
        issue(evaluation, None, path)


def test_recipient_rejects_absolute_api_paths():
    browser = object.__new__(Browser)
    with pytest.raises(FocusedError):
        browser.request("GET", "https://foreign.example/api/")


def test_doctor_failure_is_read_only(evaluation, monkeypatch):
    before = evaluation.manifest.read_bytes()
    monkeypatch.setattr(lifecycle, "source_identity", lambda: "unit-only-source")
    monkeypatch.setattr(lifecycle.Evaluation, "owned", lambda self: [])
    monkeypatch.setattr(lifecycle.Evaluation, "check_images", lambda self: None)

    def unavailable(self):
        raise FocusedError("TLS unavailable")

    monkeypatch.setattr(lifecycle.Evaluation, "ready", unavailable)
    assert lifecycle.main(["doctor", "--instance", INSTANCE]) == 1
    assert evaluation.manifest.read_bytes() == before


def test_p03_profile_has_ordered_activation_and_owned_timeout_cleanup(tmp_path):
    jobs = focused_runner.load_profile("p03-bootstrap")
    assert [j["id"] for j in jobs] == [
        "activation-unit",
        "activation-real",
        "activation-cutover",
        "p03-bootstrap",
    ]
    plan = focused_runner.prepare_job(
        jobs[-1], [jobs[-1]["selectors"][0]], INSTANCE, {}, tmp_path, "node"
    )
    assert plan["fixture_graph"]["instance"] == INSTANCE
    assert plan["fixture"] == "evaluation" and plan["timeout"] == 1800


def test_missing_intent_preflight_does_not_create_state(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, "STATE_ROOT", tmp_path / "absent")
    assert lifecycle.main(["start"]) == 1
    assert not lifecycle.STATE_ROOT.exists()


def test_recipient_rejects_remote_origin_before_loading_any_ca(tmp_path):
    with pytest.raises(FocusedError, match="loopback"):
        Browser("https://foreign.example.test:8443", tmp_path / "does-not-exist")


def test_lost_delivery_cannot_silently_mint_another_credential(evaluation, tmp_path):
    class Existing:
        def request(self, method, path):
            assert method == "GET"
            return {"credentials": [{"status": "active"}]}

    destination = tmp_path / "lost-delivery"
    with pytest.raises(FocusedError, match="already issued"):
        issue(evaluation, Existing(), destination)
    assert destination.read_bytes() == b""


def test_preflight_refuses_existing_resource_before_creating_state(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(lifecycle, "STATE_ROOT", tmp_path / "absent")
    monkeypatch.setattr(lifecycle.shutil, "which", lambda name, **kwargs: "/usr/bin/" + name)
    calls = []

    def read(argv, **kwargs):
        calls.append(argv)
        if argv[-1] == "{{.OSType}}":
            return SimpleNamespace(stdout="linux", stderr="", returncode=0)
        if "version" in argv:
            return SimpleNamespace(stdout="v2.24.4", stderr="", returncode=0)
        return SimpleNamespace(stdout="[]", stderr="", returncode=0)

    monkeypatch.setattr(lifecycle.subprocess, "run", read)
    with pytest.raises(FocusedError, match="resource is present"):
        lifecycle.preflight("api:reviewed", "console:reviewed", tmp_path, INSTANCE)
    assert not lifecycle.STATE_ROOT.exists()
    assert all("inspect" in cmd or "info" in cmd or "version" in cmd for cmd in calls)


@pytest.mark.parametrize("uncovered", [False, True], ids=["covered", "uncovered-image-volume"])
def test_fresh_preflight_accepts_real_missing_resource_diagnostics_without_mutation(
    tmp_path, monkeypatch, uncovered
):
    from types import SimpleNamespace

    monkeypatch.setattr(lifecycle, "STATE_ROOT", tmp_path / "absent")
    monkeypatch.setattr(lifecycle.shutil, "which", lambda name, **kwargs: "/usr/bin/" + name)
    monkeypatch.setattr(lifecycle, "activation_evidence", lambda path: (path, {}))

    class Port:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def bind(self, address):
            assert address == ("127.0.0.1", 0)

        def getsockname(self):
            return ("127.0.0.1", 18443)

    monkeypatch.setattr(lifecycle.socket, "socket", Port)
    calls = []

    def read(argv, **kwargs):
        calls.append(argv)
        if argv[-1] == "{{.OSType}}":
            return SimpleNamespace(stdout="linux", stderr="", returncode=0)
        if "version" in argv:
            return SimpleNamespace(stdout="v2.24.4", stderr="", returncode=0)
        if "image" in argv:
            return SimpleNamespace(
                stdout=json.dumps(
                    [
                        {
                            "Id": "unit-only-image",
                            "Config": {"Volumes": {"/unreviewed-volume": {}} if uncovered else {}},
                        }
                    ]
                ),
                stderr="",
                returncode=0,
            )
        if "ls" in argv:
            return SimpleNamespace(stdout="fictional-network", stderr="", returncode=0)
        if argv[-1] == "fictional-network":
            return SimpleNamespace(
                stdout='[{"IPAM":{"Config":null}},{"IPAM":{"Config":[{"Subnet":"172.16.0.0/12"}]}}]',
                stderr="",
                returncode=0,
            )
        diagnostic = (
            "no such volume"
            if "volume" in argv
            else "No such container"
            if "container" in argv
            else "network not found"
        )
        return SimpleNamespace(stdout="[]", stderr=diagnostic, returncode=1)

    monkeypatch.setattr(lifecycle.subprocess, "run", read)
    if uncovered:
        with pytest.raises(FocusedError, match="implicit image volume"):
            lifecycle.preflight("api:reviewed", "console:reviewed", tmp_path, INSTANCE)
        assert not lifecycle.STATE_ROOT.exists()
        return
    result = lifecycle.preflight("api:reviewed", "console:reviewed", tmp_path, INSTANCE)
    assert result["images"]["api:reviewed"] == "unit-only-image"
    assert result["subnet"].startswith("10.203.")
    assert not lifecycle.STATE_ROOT.exists()
    assert all(
        "inspect" in cmd or "info" in cmd or "version" in cmd or "ls" in cmd for cmd in calls
    )
    value = lifecycle.Evaluation(INSTANCE)
    monkeypatch.setattr(value, "command", lambda *a, **kw: (1, "no such volume"))
    assert value.inspect("volume", value.names["volume"][0]) is None


def test_owned_diagnostics_exclude_handoff_backups_and_secrets(evaluation, tmp_path):
    for name in ("handoff", "backup", "secrets"):
        (evaluation.state / name).mkdir(mode=0o700)
        (evaluation.state / name / "private-data").write_text("never archive this")
    (evaluation.state / "logs/startup.log").write_text("bounded fictional startup diagnostic")
    target = tmp_path / "diagnostics"
    evaluation.archive_diagnostics(target)
    assert {p.name for p in target.iterdir()} == {"manifest.json", "startup.log"}
    assert all(p.stat().st_mode & 0o077 == 0 for p in target.iterdir())
