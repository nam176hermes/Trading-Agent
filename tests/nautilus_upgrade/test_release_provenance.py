from __future__ import annotations

import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import zipfile

import pytest

import scripts.verify_nautilus_release_provenance as provenance


ROOT = Path(__file__).resolve().parents[2]
VERIFIER = ROOT / "scripts/verify_nautilus_release_provenance.py"
POLICY = ROOT / "engines/nautilus/v1.231-provenance-policy.json"
CACHE_ENV = "P1_U02_PROVENANCE_CACHE"


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def _tar(path: Path, members: list[tuple[tarfile.TarInfo, bytes | None]]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for member, raw in members:
            archive.addfile(member, io.BytesIO(raw) if raw is not None else None)


def _file(name: str, raw: bytes = b"fixture\n") -> tuple[tarfile.TarInfo, bytes]:
    member = tarfile.TarInfo(name)
    member.size = len(raw)
    member.mode = 0o644
    return member, raw


def _git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _bare_tagged_repo(
    tmp_path: Path, *, unsafe_link: str | None = None
) -> tuple[Path, str, str]:
    source = tmp_path / "source"
    source.mkdir()
    _git("init", "-q", cwd=source)
    _git("config", "user.name", "P1 U02 Test", cwd=source)
    _git("config", "user.email", "p1-u02@example.invalid", cwd=source)
    (source / "target.txt").write_text("target bytes\n", encoding="utf-8")
    os.symlink(unsafe_link or "target.txt", source / "link.txt")
    _git("add", "target.txt", "link.txt", cwd=source)
    _git("commit", "-q", "-m", "fixture", cwd=source)
    _git("tag", "-a", "v1.231.0", "-m", "fixture release", cwd=source)
    commit = _git("rev-parse", "HEAD", cwd=source)
    tag_object = _git("rev-parse", "refs/tags/v1.231.0", cwd=source)
    bare = tmp_path / "upstream.git"
    _git("clone", "-q", "--bare", str(source), str(bare))
    return bare, tag_object, commit


def test_missing_external_cache_is_explicitly_deferred() -> None:
    result = subprocess.run(
        [sys.executable, str(VERIFIER), "--policy", str(POLICY)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 3
    assert result.stdout == (
        "NAUTILUS_RELEASE_PROVENANCE=DEFERRED reason=cache-not-supplied\n"
    )
    assert result.stderr == ""


def test_current_policy_is_closed_and_exact() -> None:
    policy = provenance.load_policy(POLICY)

    assert policy["schema_version"] == 7
    assert policy["candidate_closure_schema"] == 7
    assert policy["source_authority"]["primary"]["role"] == "PRIMARY_BUILD_SOURCE"
    assert (
        policy["source_authority"]["cross_check"]["role"]
        == "INDEPENDENT_CROSS_CHECK_ONLY"
    )
    assert policy["release_assets"]["cpython312_linux_wheel"]["role"] == (
        "DIGEST_VERIFIED_ARTIFACT_ONLY"
    )
    assert policy["attestation_disposition"]["pep740_publish_attestations"] == (
        "PRESENT_NOT_CRYPTOGRAPHICALLY_VERIFIED"
    )
    trust = policy["cache_trust_model"]
    assert trust["host_authority"] == "COOPERATIVE_OWNER_CONTROLLED_HOST"
    assert "CONCURRENT_SAME_UID_MUTATION" in trust["out_of_scope"]
    assert "TOCTOU" in trust["out_of_scope"]


@pytest.mark.parametrize("location", ("top", "nested"))
def test_policy_duplicate_json_key_fails_closed(
    tmp_path: Path, location: str
) -> None:
    raw = POLICY.read_text(encoding="utf-8")
    if location == "top":
        raw = raw.replace(
            "{\n  \"activation_status\"",
            "{\n  \"schema_version\": 7,\n  \"activation_status\"",
            1,
        )
    else:
        raw = raw.replace(
            '"upstream": {\n    "git_object_format"',
            '"upstream": {\n    "tag": "v1.231.0",\n    "git_object_format"',
            1,
        )
    path = tmp_path / "duplicate-policy.json"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(provenance.VerificationError, match="duplicate"):
        provenance.load_policy(path)


@pytest.mark.parametrize(
    "mutation",
    (
        "schema",
        "tag_object",
        "peeled_commit",
        "primary_digest",
        "sdist_digest",
        "wheel_digest",
        "materialization_manifest",
        "build_input_manifest",
        "unknown_field",
    ),
)
def test_policy_identity_and_schema_drift_fail_closed(
    tmp_path: Path, mutation: str
) -> None:
    document = json.loads(POLICY.read_text(encoding="utf-8"))
    if mutation == "schema":
        document["schema_version"] = 8
    elif mutation == "tag_object":
        document["upstream"]["tag_object"] = "0" * 40
    elif mutation == "peeled_commit":
        document["upstream"]["peeled_commit"] = "0" * 40
    elif mutation == "primary_digest":
        document["source_authority"]["primary"]["sha256"] = "0" * 64
    elif mutation == "sdist_digest":
        document["source_authority"]["cross_check"]["sha256"] = "0" * 64
    elif mutation == "wheel_digest":
        document["release_assets"]["cpython312_linux_wheel"]["sha256"] = (
            "0" * 64
        )
    elif mutation == "materialization_manifest":
        document["source_authority"]["primary"][
            "materialization_manifest_sha256"
        ] = "0" * 64
    elif mutation == "build_input_manifest":
        document["build_input_manifest"]["sha256"] = "0" * 64
    else:
        document["latest"] = True
    path = tmp_path / "policy.json"
    _write_json(path, document)

    with pytest.raises(provenance.VerificationError, match="reviewed schema-7 policy"):
        provenance.load_policy(path)


def test_supplied_incomplete_cache_is_fail_not_deferred(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir(mode=0o700)

    result = subprocess.run(
        [
            sys.executable,
            str(VERIFIER),
            "--policy",
            str(POLICY),
            "--cache",
            str(cache),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith("NAUTILUS_RELEASE_PROVENANCE=FAIL reason=")
    assert "DEFERRED" not in result.stderr


def test_configured_cache_is_verified_offline_or_explicitly_deferred() -> None:
    configured = os.environ.get(CACHE_ENV)
    if configured is None:
        assert provenance.verify(POLICY, None) == {
            "reason": "cache-not-supplied",
            "status": "DEFERRED",
        }
        return

    receipt = provenance.verify(POLICY, Path(configured))
    assert receipt["status"] == "PASS"
    assert receipt["tag_object"] == "d3e1685e979925d7b0ffacd1b3f442547686e18f"
    assert receipt["peeled_commit"] == "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317"
    assert receipt["network"] == "DISABLED_BY_CONSTRUCTION"


@pytest.mark.parametrize(
    "attack",
    (
        "absolute",
        "drive",
        "root_drive",
        "traversal",
        "backslash",
        "symlink",
        "fifo",
        "duplicate",
        "casefold",
        "prefix",
        "casefold_prefix",
        "unicode_normalization",
        "binary",
    ),
)
def test_source_archive_layout_attacks_fail_closed(tmp_path: Path, attack: str) -> None:
    root = "source-root"
    members: list[tuple[tarfile.TarInfo, bytes | None]] = [
        _file(f"{root}/Cargo.lock")
    ]
    if attack == "absolute":
        members.append(_file("/absolute"))
    elif attack == "drive":
        members.append(_file("C:/absolute"))
    elif attack == "root_drive":
        members.append(_file(f"{root}/C:/absolute"))
    elif attack == "traversal":
        members.append(_file(f"{root}/../escape"))
    elif attack == "backslash":
        members.append(_file(f"{root}\\ambiguous"))
    elif attack == "symlink":
        member = tarfile.TarInfo(f"{root}/link")
        member.type = tarfile.SYMTYPE
        member.linkname = "Cargo.lock"
        members.append((member, None))
    elif attack == "fifo":
        member = tarfile.TarInfo(f"{root}/fifo")
        member.type = tarfile.FIFOTYPE
        members.append((member, None))
    elif attack == "duplicate":
        members.append(_file(f"{root}/Cargo.lock", b"other\n"))
    elif attack == "casefold":
        members.append(_file(f"{root}/cargo.LOCK"))
    elif attack == "prefix":
        members.append(_file(f"{root}/Cargo.lock/child"))
    elif attack == "casefold_prefix":
        members.append(_file(f"{root}/CARGO.lock/child"))
    elif attack == "unicode_normalization":
        members.extend(
            (
                _file(f"{root}/caf\N{LATIN SMALL LETTER E WITH ACUTE}"),
                _file(f"{root}/cafe\N{COMBINING ACUTE ACCENT}"),
            )
        )
    else:
        members.append(_file(f"{root}/generated.so"))
    archive = tmp_path / "source.tar.gz"
    _tar(archive, members)

    with pytest.raises(provenance.VerificationError, match="archive"):
        provenance._scan_source_archive(archive, root)


def test_wheel_rejects_symlink_member(tmp_path: Path) -> None:
    wheel = tmp_path / "demo.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        member = zipfile.ZipInfo("nautilus_trader/link")
        member.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(member, "target")

    with pytest.raises(provenance.VerificationError, match="wheel archive"):
        provenance._scan_wheel_archive(wheel)


@pytest.mark.parametrize(
    "names",
    (
        ("nautilus_trader/a", "nautilus_trader/a/b"),
        ("nautilus_trader/A", "nautilus_trader/a/b"),
        (
            "nautilus_trader/caf\N{LATIN SMALL LETTER E WITH ACUTE}",
            "nautilus_trader/cafe\N{COMBINING ACUTE ACCENT}",
        ),
    ),
)
def test_wheel_rejects_component_and_unicode_collisions(
    tmp_path: Path, names: tuple[str, str]
) -> None:
    wheel = tmp_path / "collision.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name in names:
            archive.writestr(name, b"fixture\n")

    with pytest.raises(provenance.VerificationError, match="collision"):
        provenance._scan_wheel_archive(wheel)


def test_offline_git_authority_binds_annotated_tag_and_peel(tmp_path: Path) -> None:
    git_dir, tag_object, commit = _bare_tagged_repo(tmp_path)

    receipt = provenance._verify_git_authority(
        git_dir,
        tag="v1.231.0",
        tag_object=tag_object,
        peeled_commit=commit,
    )

    assert receipt == {"tag_object": tag_object, "peeled_commit": commit}
    with pytest.raises(provenance.VerificationError, match="tag object"):
        provenance._verify_git_authority(
            git_dir,
            tag="v1.231.0",
            tag_object="0" * 40,
            peeled_commit=commit,
        )
    with pytest.raises(provenance.VerificationError, match="peeled commit"):
        provenance._verify_git_authority(
            git_dir,
            tag="v1.231.0",
            tag_object=tag_object,
            peeled_commit="0" * 40,
        )


@pytest.mark.parametrize(
    "unsafe", ("include", "promisor", "alternates", "fsck", "worktree_extension")
)
def test_git_cache_guards_reject_ambient_or_lazy_object_authority(
    tmp_path: Path, unsafe: str
) -> None:
    git_dir, tag_object, commit = _bare_tagged_repo(tmp_path)
    if unsafe == "include":
        included = tmp_path / "included.config"
        included.write_text("[remote \"spy\"]\n\tpromisor = true\n", encoding="utf-8")
        _git(f"--git-dir={git_dir}", "config", "include.path", str(included))
    elif unsafe == "promisor":
        _git(f"--git-dir={git_dir}", "config", "extensions.partialClone", "origin")
    elif unsafe == "alternates":
        alternates = git_dir / "objects/info/alternates"
        alternates.write_text(str(tmp_path / "objects") + "\n", encoding="utf-8")
    elif unsafe == "fsck":
        _git(f"--git-dir={git_dir}", "config", "fsck.missingEmail", "ignore")
    else:
        _git(f"--git-dir={git_dir}", "config", "extensions.worktreeConfig", "true")

    with pytest.raises(
        provenance.VerificationError,
        match="include|promisor|alternate|fsck|extension|worktree",
    ):
        provenance._verify_git_authority(
            git_dir,
            tag="v1.231.0",
            tag_object=tag_object,
            peeled_commit=commit,
        )


@pytest.mark.parametrize("unsafe", ("include", "promisor", "fsck"))
def test_git_cache_rejects_external_commondir_before_authority_reads(
    tmp_path: Path, unsafe: str
) -> None:
    common, tag_object, commit = _bare_tagged_repo(tmp_path)
    _git(f"--git-dir={common}", "config", "extensions.worktreeConfig", "true")
    admin = tmp_path / "worktree-admin"
    admin.mkdir()
    (admin / "commondir").write_text(str(common) + "\n", encoding="utf-8")
    (admin / "HEAD").write_text(commit + "\n", encoding="utf-8")
    if unsafe == "include":
        included = tmp_path / "included.config"
        included.write_text('[remote "spy"]\n\tpromisor = true\n', encoding="utf-8")
        unsafe_config = f"[include]\n\tpath = {included}\n"
    elif unsafe == "promisor":
        unsafe_config = '[remote "spy"]\n\tpromisor = true\n'
    else:
        unsafe_config = "[fsck]\n\tmissingEmail = ignore\n"
    (admin / "config.worktree").write_text(
        "[core]\n\tbare = true\n" + unsafe_config, encoding="utf-8"
    )

    with pytest.raises(provenance.VerificationError, match="common|worktree"):
        provenance._verify_git_authority(
            admin,
            tag="v1.231.0",
            tag_object=tag_object,
            peeled_commit=commit,
        )


def test_git_tree_rejects_casefold_component_collision(tmp_path: Path) -> None:
    bare = tmp_path / "collision.git"
    _git("init", "-q", "--bare", str(bare))
    _git(f"--git-dir={bare}", "config", "user.name", "P1 U02 Test")
    _git(f"--git-dir={bare}", "config", "user.email", "p1-u02@example.invalid")

    def object_id(*args: str, raw: bytes) -> str:
        return subprocess.run(
            ["git", f"--git-dir={bare}", *args],
            input=raw,
            check=True,
            capture_output=True,
        ).stdout.decode("ascii").strip()

    blob = object_id("hash-object", "-w", "--stdin", raw=b"fixture\n")
    child_tree = object_id(
        "mktree", "-z", raw=f"100644 blob {blob}\tchild\0".encode()
    )
    root_tree = object_id(
        "mktree",
        "-z",
        raw=(
            f"040000 tree {child_tree}\tA\0" f"100644 blob {blob}\ta\0"
        ).encode(),
    )
    commit = _git(f"--git-dir={bare}", "commit-tree", root_tree, "-m", "fixture")

    with pytest.raises(provenance.VerificationError, match="collision"):
        provenance.materialize_git_source(
            bare, commit, tmp_path / "collision.tar.gz"
        )


def test_git_tree_materialization_dereferences_safe_link_deterministically(
    tmp_path: Path,
) -> None:
    git_dir, _tag_object, commit = _bare_tagged_repo(tmp_path)
    first = tmp_path / "first.tar.gz"
    second = tmp_path / "second.tar.gz"

    first_receipt = provenance.materialize_git_source(git_dir, commit, first)
    second_receipt = provenance.materialize_git_source(git_dir, commit, second)

    assert first.read_bytes() == second.read_bytes()
    assert first_receipt == second_receipt
    assert first_receipt["symlink_count"] == 1
    link_record = first_receipt["symlink_records"][0]
    assert link_record["source_mode"] == "120000"
    assert link_record["resolved_path"] == "target.txt"
    assert link_record["output_mode"] == "0644"
    with tarfile.open(first, "r:gz") as archive:
        link = archive.getmember(f"nautilus_trader-{commit}/link.txt")
        assert link.isfile()
        extracted = archive.extractfile(link)
        assert extracted is not None
        assert extracted.read() == b"target bytes\n"


def test_git_tree_materialization_rejects_escaping_link(tmp_path: Path) -> None:
    git_dir, _tag_object, commit = _bare_tagged_repo(
        tmp_path, unsafe_link="../../outside"
    )

    with pytest.raises(provenance.VerificationError, match="symlink"):
        provenance.materialize_git_source(
            git_dir, commit, tmp_path / "source.tar.gz"
        )
