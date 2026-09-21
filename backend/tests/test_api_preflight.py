"""预检快照 + 提交入队的 API 集成测试（sqlite，不连 PostgreSQL）。"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import create_access_token
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


@pytest.fixture
def db_session(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    # 提交后真正的流水线在 BackgroundTasks 里跑，测试只验证入队，打桩掉
    monkeypatch.setattr("app.api._run_job_background", lambda *a, **k: None)

    db = TestingSessionLocal()
    db.add(
        Sample(
            name="demo-good-r1",
            description="合格样例",
            is_broken=False,
            fastq_content=GOOD_FASTQ,
        )
    )
    db.commit()

    yield db

    db.close()
    app.dependency_overrides.clear()


@pytest.fixture
def client(db_session):
    return TestClient(app)


def _auth(username: str, role: str) -> dict:
    token = create_access_token(username, role)
    return {"Authorization": f"Bearer {token}"}


BIOOPS = _auth("bioops", "bioops")
AUDITOR = _auth("auditor", "auditor")


def test_preflight_sample_snapshot(client, db_session):
    res = client.post("/api/jobs/preflight", json={"sampleId": 1}, headers=BIOOPS)
    assert res.status_code == 200
    snap = res.json()
    assert snap["source"] == "sample"
    assert snap["sample_name"] == "demo-good-r1"
    assert snap["is_custom"] is False
    assert snap["text_empty"] is False
    assert snap["text_length"] == len(GOOD_FASTQ)
    assert snap["username"] == "bioops"
    assert snap["role"] == "bioops"


def test_preflight_custom_snapshot(client):
    res = client.post(
        "/api/jobs/preflight", json={"fastqText": GOOD_FASTQ}, headers=BIOOPS
    )
    assert res.status_code == 200
    snap = res.json()
    assert snap["source"] == "custom"
    assert snap["is_custom"] is True
    assert snap["sample_name"] == "自定义输入"  # 自定义标记
    assert snap["text_empty"] is False
    assert snap["text_length"] == len(GOOD_FASTQ.strip())
    assert snap["username"] == "bioops"


def test_preflight_blank_text_is_empty_check(client):
    # 服务端粗检：纯空白视为无输入
    res = client.post(
        "/api/jobs/preflight", json={"fastqText": "   \n "}, headers=BIOOPS
    )
    assert res.status_code == 400


def test_preflight_no_input_400(client):
    res = client.post("/api/jobs/preflight", json={}, headers=BIOOPS)
    assert res.status_code == 400


def test_preflight_missing_sample_404(client):
    res = client.post(
        "/api/jobs/preflight", json={"sampleId": 999}, headers=BIOOPS
    )
    assert res.status_code == 404


def test_preflight_auditor_forbidden(client):
    res = client.post(
        "/api/jobs/preflight", json={"fastqText": GOOD_FASTQ}, headers=AUDITOR
    )
    assert res.status_code == 403


def test_cancel_creates_nothing_then_confirm_adds_one(client, db_session):
    """自测主线：预检（取消）无新单；确认提交后历史多一条。"""

    def history_count():
        return db_session.query(Job).count()

    assert history_count() == 0

    # 用户点“提交”→ 只拿到快照后取消，不应产生任何作业
    snap = client.post(
        "/api/jobs/preflight", json={"sampleId": 1}, headers=BIOOPS
    )
    assert snap.status_code == 200
    assert history_count() == 0
    assert client.get("/api/jobs", headers=BIOOPS).json() == []

    # 用户确认 → 真正入队，历史 +1
    created = client.post("/api/jobs", json={"sampleId": 1}, headers=BIOOPS)
    assert created.status_code == 201
    job = created.json()
    assert job["sample_name"] == "demo-good-r1"
    assert job["created_by"] == "bioops"
    assert job["status"] == "pending"

    assert history_count() == 1
    history = client.get("/api/jobs", headers=BIOOPS).json()
    assert len(history) == 1
    assert history[0]["id"] == job["id"]


def test_auditor_cannot_confirm_either(client, db_session):
    res = client.post("/api/jobs", json={"sampleId": 1}, headers=AUDITOR)
    assert res.status_code == 403
    assert db_session.query(Job).count() == 0
