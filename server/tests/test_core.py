"""Unit tests that need no network or model: guide validation, evidence search, sessions and timers."""
import json
import time
from pathlib import Path

import pytest

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "pasta"


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    from crv_guide_mcp import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.sqlite3")
    monkeypatch.setattr(config, "VIDEOS_DIR", tmp_path / "videos")


def test_sample_loads_and_guide_cites_real_frames():
    from crv_guide_mcp import extract
    w = extract.load(FIX)
    g = json.loads((FIX / "guide.json").read_text())
    frames = {f.index: f.t for f in w.frames}
    assert len(w.segments) > 100 and len(w.frames) > 50
    for st in g["steps"]:
        assert st["frame"] in frames                      # cites a frame that exists
        assert st["start_s"] - 3 <= frames[st["frame"]] <= st["end_s"] + 3   # inside its step
        assert all(0 <= i < len(w.segments) for i in st["evidence"])


def test_validate_repairs_bad_model_output():
    from crv_guide_mcp import extract, guide
    w = extract.load(FIX)
    bad = guide.GuideOut(
        title="t", summary="s", ingredients=[], equipment=[],
        steps=[guide.StepOut(title="a", say="b", look_for="c", start_s=130, end_s=160, frame=9999, evidence=[1, 99999]),
               guide.StepOut(title="z", say="y", look_for="x", start_s=20, end_s=-5, frame=1, wait_s=0)],
    )
    out = guide.validate(bad, w)
    assert [s["step_number"] for s in out["steps"]] == [1, 2]
    assert out["steps"][0]["start_s"] == 20                 # sorted by time
    assert out["steps"][0]["end_s"] >= out["steps"][0]["start_s"]
    assert out["steps"][1]["frame"] != 9999                 # invented frame replaced by a real one
    assert out["steps"][1]["evidence"] == [1]               # invented line dropped
    assert out["steps"][0]["wait_s"] is None


def test_evidence_finds_water_quantity():
    from crv_guide_mcp import evidence, extract
    w = extract.load(FIX)
    g = json.loads((FIX / "guide.json").read_text())
    hits = evidence.search(evidence.build_passages(w, g), "how much water do I add", k=4)
    text = " ".join(p.text.lower() for _, p in hits)
    assert "water" in text and ("three-quarters" in text or "three quarters" in text)


def test_sessions_survive_and_timers_count_down():
    from crv_guide_mcp import store
    gid = store.save_guide("sample:pasta", "sample:pasta", str(FIX), json.loads((FIX / "guide.json").read_text()))
    sid = store.create_session(gid)
    s = store.get_session(sid)
    assert store.update_session(sid, step_index=1, completed=[0], status="active", expected_revision=s["revision"])
    assert not store.update_session(sid, step_index=2, completed=[0, 1], status="active", expected_revision=s["revision"])  # stale revision
    t1 = store.add_timer(sid, 120, "rest", "req-1")
    t2 = store.add_timer(sid, 120, "rest", "req-1")      # same request id: no duplicate
    assert t1["id"] == t2["id"]
    timers = store.timers_for(sid)
    assert len(timers) == 1 and 0 < timers[0]["remaining_s"] <= 120
    assert store.latest_active_session()["id"] == sid
