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



def test_answer_is_pinned_to_the_line_that_says_it():
    from crv_guide_mcp import evidence, extract
    w = extract.load(FIX)
    g = json.loads((FIX / "guide.json").read_text())
    step2 = g["steps"][1]
    line = evidence.best_line(w.segments, step2["evidence"], "How much water did she use?")
    assert "three quarters of a cup" in line.text and extract.clock(line.start) == "1:24"
    thumb = evidence.best_line(w.segments, [57, 58, 59], "how she makes the shape with her thumb")
    assert "thumb" in thumb.text                            # the rarer word wins over "shape"
    long_q = "pressing thumb into dough ball and dragging back to create orecchiette shape"
    assert "thumb" in evidence.best_line(w.segments, [56, 57, 58, 59], long_q).text
    topic = frozenset(evidence.tokens(g["title"]))          # the dish name points at no particular moment
    q = "pressing the thumb into the dough to shape orecchiette"
    assert "thumb" in evidence.best_line(w.segments, [56, 57, 58, 59], q, ignore=topic).text
    assert evidence.best_line(w.segments, step2["evidence"], "oven temperature") is None


def test_amount_questions_need_their_subject_in_the_video():
    from crv_guide_mcp import evidence, extract
    w = extract.load(FIX)
    g = json.loads((FIX / "guide.json").read_text())
    passages = evidence.build_passages(w, g)
    assert evidence.search(passages, "How much sugar does she add?") == []        # no sugar in this recipe
    assert evidence.unmentioned("How much sugar does she add?", passages) == ["sugar"]
    hits = evidence.search(passages, "How much water did she use?")
    assert hits and all("water" in p.toks for _, p in hits)
    assert evidence.search(passages, "How long does the dough rest?")


def test_numbers_in_steps_are_checked_against_the_narration():
    from crv_guide_mcp import checks, extract, guide
    w = extract.load(FIX)
    a = checks.mentions("three-quarters of a cup of cold water")[0]
    assert a.numbers == {0.75} and a.unit == "cup" and "water" in a.what
    assert 1800.0 in checks.mentions("about half an hour")[0].seconds        # same as "30 minutes"
    bad = guide.GuideOut(
        title="t", summary="s", equipment=[],
        ingredients=[guide.IngredientOut(item="sugar", amount="99 cups", evidence=[0])],
        steps=[guide.StepOut(title="Invented", say="Add 99 cups of sugar and rest for 12 hours.", look_for="x",
                             start_s=0, end_s=30, frame=1, wait_s=43200, evidence=[0])],
    )
    out = guide.validate(bad, w)
    st = out["steps"][0]
    assert "99 cups" in st["unverified"] and "12 hours" in st["unverified"]
    assert st["wait_s"] is None and st["timer_dropped_s"] == 43200    # no timer from a duration nobody said
    assert out["ingredients"][0]["unverified"] is True
    real = checks.check_guide(json.loads((FIX / "guide.json").read_text()), w)
    assert real["steps"][1]["unverified"] == [] and real["steps"][3]["wait_s"] == 1800   # 3/4 cup, half an hour: said

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



def test_session_tools_carry_the_mcp_apps_view():
    import asyncio
    from crv_guide_mcp import server
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    for name in ["start_session", "get_session", "move_step", "ask_source", "show_moment", "set_timer", "get_recipe"]:
        assert tools[name].meta["ui"]["resourceUri"] == "ui://second-look/cook.html"
    assert "ui" not in (tools["import_video"].meta or {})
    contents = list(asyncio.run(server.mcp.read_resource("ui://second-look/cook.html")))
    assert contents[0].mime_type == "text/html;profile=mcp-app"
    html = contents[0].content
    assert "ui/initialize" in html and "ui/notifications/tool-result" in html and "tools/call" in html



def test_amounts_must_match_unit_and_ingredient():
    from types import SimpleNamespace as NS
    from crv_guide_mcp import checks
    said = NS(segments=[NS(index=0, start=0.0, end=4.0, text="Add two teaspoons of salt."),
                        NS(index=1, start=4.0, end=8.0, text="Then two cups of flour, and fourteen minutes in the oven.")])
    step = lambda say: {"say": say, "start_s": 0.0, "end_s": 8.0, "evidence": [0, 1], "wait_s": None}  # noqa: E731
    g = checks.check_guide({"steps": [step("Add two cups of sugar."), step("Add two teaspoons of salt."),
                                      step("Add two cups of flour."), step("Bake for fourteen minutes.")]}, said)
    assert [s["unverified"] for s in g["steps"]] == [["two cups"], [], [], []]   # sugar is not flour, cups are not teaspoons


def test_each_quoted_line_is_shown_once():
    import asyncio
    from crv_guide_mcp import server, store
    gid = store.save_guide("sample:pasta", "sample:pasta", str(FIX), json.loads((FIX / "guide.json").read_text()))
    sid = store.create_session(gid)
    for q in ["How much water did she use?", "how long does she knead the dough?"]:
        r = asyncio.run(server.ask_source(question=q, session_id=sid))
        said = [e["said_at"] for e in r.structured_content["evidence"] if "said_at" in e]
        assert said and len(said) == len(set(said))   # a step and a transcript window quoting one line make one card


def test_recipe_cites_only_amounts_the_narration_states():
    import asyncio
    from crv_guide_mcp import checks, extract, server, store
    w = extract.load(FIX)
    assert extract.clock(w.segments[checks.said_where("3/4 cup", "Cold water", w, [22, 25, 29])].start) == "1:24"
    assert checks.said_where("1 tsp", "Fresh thyme", w) is None        # "one of the last things" is not one teaspoon
    gid = store.save_guide("sample:pasta", "sample:pasta", str(FIX), json.loads((FIX / "guide.json").read_text()))
    sid = store.create_session(gid)
    r = asyncio.run(server.get_recipe(session_id=sid)).structured_content["recipe"]
    rows = {i["item"]: i for i in r["ingredients"]}
    assert rows["Cold water"]["source"] == "said" and rows["Cold water"]["said_at"] == "1:24"
    assert rows["All-purpose flour"]["said_at"] == "0:44"               # "two cups of each", cited for the flour
    assert rows["Fresh thyme"]["source"] == "not_said" and rows["Leeks"]["source"] == "unstated"
    assert len(r["steps"]) == 13
