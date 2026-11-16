import numpy as np

from app.core import captions, reframe, select, signals, transcript
from app.ingest.vod import comment_timestamps
from app.rules import campaign as campaigns
from app.rules import check


def _words(n=120, step=0.5):
    out = []
    for i in range(n):
        text = f"w{i}" + ("." if i % 8 == 7 else "")
        out.append({"text": text, "start": i * step, "end": i * step + 0.4, "speaker": "A"})
    return out


def test_sentences_split_on_punctuation():
    sents = transcript.to_sentences(_words())
    assert len(sents) == 15
    assert sents[0].text.endswith("w7.") and sents[1].w0 == 8
    assert "[0] 0:00 Speaker A:" in transcript.render_for_llm(sents)


def test_snap_respects_length_limits_and_sentence_edges():
    sents = transcript.to_sentences(_words())
    camp = campaigns.load("lhm")
    short = select.Window(0, 2, 2, "llm")
    select.snap(short, sents, camp, 60)
    assert camp.clip.min_seconds <= short.end - short.start <= camp.clip.max_seconds
    long = select.Window(1, 0, 14, "llm")
    camp.clip.max_seconds = 30
    select.snap(long, sents, camp, 60)
    assert long.end - long.start <= 30
    assert any(abs(long.end - s.end) < 0.4 for s in sents)


def test_dedupe_keeps_best_of_overlapping():
    a = select.Window(0, 0, 1, "llm", start=0, end=40, score=0.5)
    b = select.Window(1, 0, 1, "llm", start=10, end=50, score=0.9)
    c = select.Window(2, 0, 1, "llm", start=100, end=140, score=0.3)
    assert [w.id for w in select.dedupe([a, b, c])] == [1, 2]


def test_final_score_ignores_missing_signals():
    w = select.Window(0, 0, 1, "signals", signals={"heatmap": 0.0, "loudness": 0.8})
    weights = {"judge": 0.6, "heatmap": 0.2, "loudness": 0.2}
    assert abs(select.final_score(w, weights, {"loudness"}) - 0.8) < 1e-9


def test_signal_tracks_and_peaks():
    db = np.full(300, -30.0)
    db[200:205] = -8
    tracks = signals.build({"duration": 299, "heatmap": [], "comment_timestamps":
                            [{"t": 100, "likes": 50, "text": "1:40 lol"}]}, db)
    assert tracks["loudness"][202] > 0.8 and tracks["comments"][105] == 1
    peaks = [t for t, _ in signals.peaks(tracks, {"loudness": 1.0, "comments": 1.0})]
    assert any(190 <= t <= 215 for t in peaks) and any(95 <= t <= 130 for t in peaks)


def test_comment_timestamps():
    got = comment_timestamps([{"text": "12:34 was wild, also 1:02:03", "like_count": 5},
                              {"text": "ratio 16:9 no"}], 4000)
    assert sorted(c["t"] for c in got) == [754, 3723]   # "16:9" is not a timestamp


def test_ass_captions_are_clip_relative(tmp_path):
    p = captions.write_ass(_words(), 10.0, 20.0, tmp_path / "c.ass")
    body = p.read_text()
    assert "Dialogue: 0,0:00:00.00" in body and "W20" in body and "W19 " not in body
    assert body.count("Dialogue:") == 20


def test_crop_follows_face_and_cuts_on_speaker_change():
    left, right = [(0.25, 0.05)], [(0.75, 0.05)]
    segs = reframe._segments([left] * 6 + [[]] + [right] * 6)
    assert len(segs) == 2 and abs(segs[0][1] - 0.25) < 0.01 and abs(segs[1][1] - 0.75) < 0.01
    assert segs[1][0] == 3.5
    expr = reframe._crop_x_expr(segs, 1920, 606)
    assert expr.startswith("if(lt(t\\,3.50)\\,177\\,")


def test_rules_lhm():
    camp = campaigns.load("lhm")
    cap = check.build_caption(camp, "He had an answer for EVERYTHING 💀", 0)
    assert cap.splitlines()[0] == "Full episode on @lethimmansplain"
    good = check.check(camp, caption=cap, duration=45, render={"watermark": True})
    assert check.compliant(good)
    bad = check.check(camp, caption="nice clip", duration=10, render={"watermark": False}, ad_like=True)
    failed = {r["rule"] for r in bad if not r["ok"]}
    assert failed == {"length", "caption first line tag", "hashtags", "watermark", "not ad-like"}


def test_watermark_forbidden_campaign():
    camp = campaigns.load("lhm")
    camp.forbid.append("watermark")
    r = check.check(camp, caption=check.build_caption(camp, "x"), duration=45, render={"watermark": True})
    assert any(x["rule"] == "no watermark" and not x["ok"] for x in r)
