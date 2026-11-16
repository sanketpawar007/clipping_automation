import json

from app.scanner import board, scan


def _page(payload: str) -> str:
    return f"<script>self.__next_f.push([1,{json.dumps(payload)}])</script>"


def test_parse_board_dedupes_and_reads_campaigns():
    a = {"id": "a", "title": "TJR Clipping", "payoutSortRaw": 0.75}
    b = {"id": "b", "title": "GTA 6 clips", "payoutSortRaw": 2}
    html = _page('1:{"initialResults":[{"campaigns":' + json.dumps([a, b]) + "}]}") + \
        _page('2:{"campaigns":' + json.dumps([b]) + "}")
    got = board.parse_board(html)
    assert sorted(c["id"] for c in got) == ["a", "b"]


def test_parse_campaign_detail():
    data = {"id": "x", "name": "N", "status": "active",
            "configuration": {"budget": 100, "payoutModel": [{"platform": "youtube", "rate": 50}]}}
    html = _page('9:{"state":{"data":null},"other":{"data":' + json.dumps(data) + "}}")
    d = board.parse_campaign(html, "x")
    assert d["payout_model"][0]["rate"] == 50


def test_keywords():
    assert scan.keyword_hits({"title": "Best GTA 6 moments", "description": "Vice City"}) == \
        ["gta", "vice city"]
    assert scan.keyword_hits({"title": "Podcast clipping", "description": "regatta"}) == []


def test_format_handles_pool_campaign_without_rates():
    c = {"id": "p", "title": "Pool", "type": "pool", "budgetTotalRaw": 9000, "platforms": ["x"]}
    msg = scan._format(c, {"payout_model": [{"platform": "x"}]}, ["KEYWORD gta"])
    assert "Pool campaign" in msg and "Per platform" not in msg
