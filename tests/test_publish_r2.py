"""The R2 publisher against an in-memory S3 stand-in: configuration, preflight, plan vs publish, skipping what
the bucket has, refusals (no yes, drafts, invalid releases, differing objects), catalog last and signed, the
previous catalog kept, rollback as a new sequence. Never touches a network or a real SDK."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assetforge.core.catalog import Library
from assetforge.errors import (ConflictError, PolicyError, PublishAccessDeniedError, PublishBucketMissingError,
                               PublishNotConfiguredError)  # fmt: skip
from assetforge.publish import r2
from assetforge.story import catalog_sign, contract
from assetforge.story import contract_models as cm
from assetforge.story.project import StoryProject
from test_story_contract import edit_script, stock

CFG = r2.R2Config(bucket="creative-library-v2-stage-vn", account_id="acct0000abcd", access_key="AK", secret_key="SK")


class S3Error(Exception):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}


class FakeS3:
    def __init__(self, bucket: str = CFG.bucket, *, denied: bool = False) -> None:
        self.bucket, self.denied = bucket, denied
        self.objects: dict[str, dict[str, Any]] = {}
        self.puts: list[str] = []

    def _check(self, bucket: str) -> None:
        if self.denied:
            raise S3Error("AccessDenied", 403)
        if bucket != self.bucket:
            raise S3Error("NoSuchBucket", 404)

    def head_bucket(self, Bucket: str) -> dict[str, Any]:  # noqa: N803 - boto3's names
        self._check(Bucket)
        return {}

    def head_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        self._check(Bucket)
        if Key not in self.objects:
            raise S3Error("404", 404)
        return {"ContentLength": len(self.objects[Key]["body"]), "Metadata": self.objects[Key]["meta"]}

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        self._check(Bucket)
        if Key not in self.objects:
            raise S3Error("NoSuchKey", 404)
        return {"Body": io.BytesIO(self.objects[Key]["body"])}

    def put_object(self, Bucket: str, Key: str, Body: bytes, ContentType: str, CacheControl: str, Metadata: dict[str, str]) -> None:  # noqa: N803
        self._check(Bucket)
        self.objects[Key] = {"body": Body, "type": ContentType, "cache": CacheControl, "meta": Metadata}
        self.puts.append(Key)

    def catalog(self, home: Path) -> dict[str, Any]:
        return catalog_sign.verify(json.loads(self.objects["catalog/current.json"]["body"]), catalog_sign.load_public(home))


@pytest.fixture
def released(lib: Library, tmp_path: Path) -> StoryProject:
    import shutil

    from test_story_contract import SAMPLE

    shutil.copytree(SAMPLE, tmp_path / "kite")
    project = StoryProject(tmp_path / "kite")
    stock(lib, project, tmp_path)
    assert contract.compile_release(lib, project).valid
    return project


def rel(project: StoryProject, rid: str = "r0001") -> Path:
    return project.root / "releases" / rid


# ---------------------------------------------------------------------- configuration


def test_config_reads_a_foreign_env_file_without_copying(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    foreign = tmp_path / "other-repo" / ".env"
    foreign.parent.mkdir()
    foreign.write_text("R2_ACCESS_KEY=ak123\nR2_SECRET_KEY='sk456'\nexport R2_ACCOUNT_ID=acct7890\nOTHER_SECRET=zzz\n")
    monkeypatch.setenv("ASSETFORGE_R2_ENV_FILE", str(foreign))
    monkeypatch.setenv("ASSETFORGE_R2_BUCKET", "creative-library-v2-stage-vn")
    cfg = r2.resolve_config(home)
    assert (cfg.access_key, cfg.secret_key, cfg.account_id) == ("ak123", "sk456", "acct7890")
    assert cfg.endpoint == "https://acct7890.r2.cloudflarestorage.com"
    shown = json.dumps(cfg.describe()) + repr(cfg)
    assert "ak123" not in shown and "sk456" not in shown and "acct7890" not in shown
    monkeypatch.setenv("ASSETFORGE_R2_ACCESS_KEY", "direct")
    assert r2.resolve_config(home).access_key == "direct"  # a direct setting wins over the file
    assert not list(home.glob("**/.env")) if home.exists() else True  # nothing was copied into the library
    monkeypatch.delenv("ASSETFORGE_R2_BUCKET")
    with pytest.raises(PublishNotConfiguredError, match="ASSETFORGE_R2_BUCKET"):
        r2.resolve_config(home)
    monkeypatch.setenv("ASSETFORGE_R2_ENV_FILE", str(tmp_path / "nope.env"))
    with pytest.raises(PublishNotConfiguredError):
        r2.resolve_config(home)


def test_boto3_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "boto3", None)
    with pytest.raises(PublishNotConfiguredError) as err:
        r2.make_client(CFG)
    assert err.value.hint == "uv sync --extra publish"


def test_preflight_tells_missing_from_denied() -> None:
    assert r2.preflight(FakeS3(), CFG)["reachable"]
    with pytest.raises(PublishBucketMissingError):
        r2.preflight(FakeS3(bucket="creative-library-v2-stage"), CFG)
    with pytest.raises(PublishAccessDeniedError):
        r2.preflight(FakeS3(denied=True), CFG)


# ---------------------------------------------------------------------- plan and publish


def test_plan_writes_nothing(home: Path, released: StoryProject) -> None:
    s3 = FakeS3()
    plan = r2.plan(rel(released), home=home, client=s3, cfg=CFG)
    assert s3.puts == [] and plan["publishable"] and plan["remote"] == "checked"
    assert plan["totals"]["upload"]["count"] == len(plan["objects"]) and plan["totals"]["upload"]["bytes"] > 0
    assert plan["catalog"] == {"key": "catalog/current.json", "current_sequence": None, "next_sequence": 1}
    assert plan["objects"][-1]["key"].endswith("/r0001/release.json")


def test_refusals_write_nothing(home: Path, lib: Library, released: StoryProject) -> None:
    s3 = FakeS3()
    with pytest.raises(PolicyError):
        r2.publish(rel(released), yes=False, home=home, client=s3, cfg=CFG)
    contract.compile_release(lib, released, allow_missing=True)  # identical: no draft; make one on purpose
    edit_script(released, lambda d: d["scenes"][0]["nodes"].insert(0, {"kind": "cg", "cg": "brand_new", "text": "x"}))
    assert contract.compile_release(lib, released, allow_missing=True).status == "draft"
    with pytest.raises(PolicyError, match="draft"):
        r2.publish(rel(released, "draft"), yes=True, home=home, client=s3, cfg=CFG)
    # a release whose files no longer match their descriptors does not validate
    graph = next(rel(released).glob("ch1/graph.json"))
    graph.write_bytes(graph.read_bytes().replace(b"s_roof.n0", b"s_roof.nX", 1))
    with pytest.raises(PolicyError, match="does not validate"):
        r2.publish(rel(released), yes=True, home=home, client=s3, cfg=CFG)
    assert s3.puts == []


def test_publish_uploads_then_signs_the_catalog_last(home: Path, released: StoryProject) -> None:
    s3 = FakeS3()
    receipt = r2.publish(rel(released), yes=True, home=home, client=s3, cfg=CFG)
    release_key = "stories/kite-season/releases/r0001/release.json"
    assert s3.puts[-2:] == ["catalog/history/1.json", "catalog/current.json"]  # catalog last
    assert s3.puts[-3] == release_key  # the manifest only after every object it names
    blobs = [k for k in s3.puts if k.startswith("blobs/")]
    assert blobs and max(s3.puts.index(b) for b in blobs) < min(s3.puts.index(k) for k in s3.puts if k.startswith("stories/"))
    assert s3.objects[release_key]["cache"] == r2.IMMUTABLE and s3.objects["catalog/current.json"]["cache"] == r2.MUTABLE
    assert all(o["meta"]["sha256"] == cm.sha256(o["body"]) for o in s3.objects.values())
    catalog = s3.catalog(home)
    assert catalog["catalogSequence"] == 1 and receipt["sequence"] == 1
    story = catalog["stories"][0]
    assert story["storyId"] == "kite-season" and story["current"] == "r0001" and story["title"] == "Kite Season"
    assert story["releases"][0]["manifest"]["key"] == release_key and story["thumbnail"]["file"]["key"] in s3.objects
    # receipts and the index record it
    assert json.loads((released.root / "releases" / "publish.json").read_text())["receipts"][0]["release"] == "r0001"
    assert contract.releases_view(released)["releases"][0]["published"]["sequence"] == 1


def test_republish_skips_everything_and_keeps_the_previous_catalog(home: Path, released: StoryProject) -> None:
    s3 = FakeS3()
    r2.publish(rel(released), yes=True, home=home, client=s3, cfg=CFG)
    first = s3.objects["catalog/current.json"]["body"]
    s3.puts.clear()
    receipt = r2.publish(rel(released), yes=True, home=home, client=s3, cfg=CFG)
    assert receipt["uploaded"] == 0 and s3.puts == ["catalog/history/2.json", "catalog/current.json"]
    assert s3.objects["catalog/history/1.json"]["body"] == first and s3.catalog(home)["catalogSequence"] == 2


def test_existing_blob_is_skipped(home: Path, released: StoryProject) -> None:
    _, objs = contract.release_objects(rel(released))
    blob = next(o for o in objs if o.role == "blob")
    s3 = FakeS3()
    s3.put_object(Bucket=CFG.bucket, Key=blob.key, Body=blob.path.read_bytes(), ContentType=blob.media_type, CacheControl="x", Metadata={})
    s3.puts.clear()
    plan = r2.plan(rel(released), home=home, client=s3, cfg=CFG)
    assert next(o for o in plan["objects"] if o["key"] == blob.key)["action"] == "skip"
    r2.publish(rel(released), yes=True, home=home, client=s3, cfg=CFG)
    assert blob.key not in s3.puts


def test_never_overwrites_a_differing_release(home: Path, released: StoryProject) -> None:
    s3 = FakeS3()
    key = "stories/kite-season/releases/r0001/release.json"
    s3.put_object(Bucket=CFG.bucket, Key=key, Body=b'{"other":1}', ContentType="application/json", CacheControl="x", Metadata={})
    s3.puts.clear()
    assert r2.plan(rel(released), home=home, client=s3, cfg=CFG)["conflicts"] == [key]
    with pytest.raises(ConflictError):
        r2.publish(rel(released), yes=True, home=home, client=s3, cfg=CFG)
    assert s3.puts == [] and s3.objects[key]["body"] == b'{"other":1}'


def test_foreign_catalog_is_refused(home: Path, released: StoryProject) -> None:
    s3 = FakeS3()
    catalog_sign.load_or_create_key(home)
    foreign = catalog_sign.sign({"kind": "vn.catalog", "schemaVersion": "2.0", "catalogSequence": 4, "stories": []},
                                Ed25519PrivateKey.generate())  # fmt: skip
    s3.put_object(Bucket=CFG.bucket, Key="catalog/current.json", Body=cm.canonical(foreign), ContentType="application/json",
                  CacheControl="x", Metadata={})  # fmt: skip
    s3.puts.clear()
    with pytest.raises(PolicyError, match="another key"):
        r2.publish(rel(released), yes=True, home=home, client=s3, cfg=CFG)
    assert s3.puts == []


def test_rollback_is_a_new_sequence(home: Path, lib: Library, released: StoryProject) -> None:
    s3 = FakeS3()
    r2.publish(rel(released), yes=True, home=home, client=s3, cfg=CFG)
    edit_script(released, lambda d: d["scenes"][0]["nodes"][0].update(text="The bell is still ringing."))
    assert contract.compile_release(lib, released).release_id == "r0002"
    r2.publish(rel(released, "r0002"), yes=True, home=home, client=s3, cfg=CFG)
    assert s3.catalog(home)["stories"][0]["current"] == "r0002"
    s3.puts.clear()
    receipt = r2.publish(rel(released, "r0001"), yes=True, home=home, client=s3, cfg=CFG)  # roll back
    catalog = s3.catalog(home)
    assert receipt["uploaded"] == 0 and catalog["catalogSequence"] == 3 and catalog["stories"][0]["current"] == "r0001"
    assert [r["releaseId"] for r in catalog["stories"][0]["releases"]] == ["r0001", "r0002"]
    assert {"catalog/history/1.json", "catalog/history/2.json", "catalog/history/3.json"} <= set(s3.objects)
