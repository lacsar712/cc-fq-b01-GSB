"""API tests for the submit flow: server precheck snapshot → confirm/cancel.

Runs against SQLite (no Postgres needed). The background pipeline's SessionLocal
is pointed at the same in-memory DB so a confirmed job actually enqueues/runs.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.api as api_module
from app.database import Base, get_db
from app.main import app
from app.models import Job, Sample


GOOD_FASTQ = """@SEQ1
ACGTACGT
+
IIIIHHHH
@SEQ2
NNNNACGT
+
IIIIIIII
"""


@pytest.fixture()
def env(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    # _run_job_background opens its own session via app.api.SessionLocal.
    monkeypatch.setattr(api_module, "SessionLocal", TestingSession)

    db = TestingSession()
    db.add(
        Sample(
            name="demo-good-r1",
            description="合格样例",
            is_broken=False,
            fastq_content=GOOD_FASTQ,
        )
    )
    db.commit()
    sample_id = db.query(Sample).filter_by(name="demo-good-r1").one().id
    db.close()

    client = TestClient(app)
    yield client, TestingSession, sample_id
    app.dependency_overrides.clear()


def _login(client, username, password):
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def _job_count(client, headers):
    res = client.get("/api/jobs", headers=headers)
    assert res.status_code == 200, res.text
    return len(res.json())


def test_precheck_snapshot_for_sample(env):
    client, _, sample_id = env
    headers = _login(client, "bioops", "fastq123456")

    res = client.post("/api/jobs/precheck", json={"sampleId": sample_id}, headers=headers)
    assert res.status_code == 200, res.text
    snap = res.json()
    assert snap["sample_id"] == sample_id
    assert snap["sample_name"] == "demo-good-r1"
    assert snap["text_empty"] is False
    assert snap["text_length"] == len(GOOD_FASTQ)
    assert snap["requested_by"] == "bioops"


def test_precheck_snapshot_for_custom_text(env):
    client, _, _ = env
    headers = _login(client, "bioops", "fastq123456")

    res = client.post("/api/jobs/precheck", json={"fastqText": GOOD_FASTQ}, headers=headers)
    assert res.status_code == 200, res.text
    snap = res.json()
    assert snap["sample_id"] is None
    assert snap["sample_name"] == "自定义输入"
    assert snap["text_empty"] is False

    empty = client.post("/api/jobs/precheck", json={"fastqText": "   "}, headers=headers)
    assert empty.status_code == 200, empty.text
    assert empty.json()["text_empty"] is True


def test_precheck_sample_not_found(env):
    client, _, _ = env
    headers = _login(client, "bioops", "fastq123456")
    res = client.post("/api/jobs/precheck", json={"sampleId": 9999}, headers=headers)
    assert res.status_code == 404


def test_precheck_auditor_forbidden(env):
    client, _, sample_id = env
    headers = _login(client, "auditor", "audit123456")
    res = client.post("/api/jobs/precheck", json={"sampleId": sample_id}, headers=headers)
    assert res.status_code == 403
    # 审计员同样不能真正提交
    res = client.post("/api/jobs", json={"sampleId": sample_id}, headers=headers)
    assert res.status_code == 403


def test_cancel_creates_nothing_confirm_enqueues(env):
    client, TestingSession, sample_id = env
    headers = _login(client, "bioops", "fastq123456")

    before = _job_count(client, headers)

    # 预检后取消：不再调用任何接口，历史不能多单
    res = client.post("/api/jobs/precheck", json={"sampleId": sample_id}, headers=headers)
    assert res.status_code == 200, res.text
    assert _job_count(client, headers) == before
    db = TestingSession()
    assert db.query(Job).count() == before
    db.close()

    # 确认：按快照对应的同一 body 调 POST /api/jobs，历史多一条
    created = client.post("/api/jobs", json={"sampleId": sample_id}, headers=headers)
    assert created.status_code == 201, created.text
    job_id = created.json()["id"]
    assert _job_count(client, headers) == before + 1

    history = client.get("/api/jobs", headers=headers).json()
    assert history[0]["id"] == job_id  # 最新一条在最前
    assert history[0]["sample_name"] == "demo-good-r1"
    assert history[0]["created_by"] == "bioops"
    # TestClient 会同步跑完后台任务；重新拉取确认确实已入队执行
    detail = client.get(f"/api/jobs/{job_id}", headers=headers).json()
    assert detail["status"] == "success"
