from services.agents.jobs import job_label, summarize


def _job(titles, market="Cheyenne, WY", **inp):
    return {"id": "x", "input": {"market": market, **inp},
            "outputs": [{"id": f"s{i}", "kind": "json", "value": {"title": t}} for i, t in enumerate(titles)]
            + [{"id": "cap", "kind": "text", "value": "caption"}]}


def test_label_is_city_and_headline():
    assert job_label(_job(["Fig and Olive to open downtown"])) == "Cheyenne · Fig and Olive to open downtown"


def test_label_counts_extra_stories_and_dedupes():
    j = _job(["Dunkin' opens Tuesday", "Data center rules", "Dunkin' opens Tuesday", "North Cheyenne plats"])
    assert job_label(j) == "Cheyenne · Dunkin' opens Tuesday (+2 more)"


def test_long_headline_is_trimmed():
    assert job_label(_job(["A" * 200])).endswith("…")
    assert len(job_label(_job(["A" * 200], market=""))) == 90


def test_falls_back_to_input_topic_then_blank():
    assert job_label({"input": {"topic": "Hormozi clip"}, "outputs": []}) == "Hormozi clip"
    assert job_label({"input": {}, "outputs": []}) == ""


def test_summarize_carries_label_but_not_output_values():
    s = summarize(_job(["Lucha libre spot opens"], market="Springfield, MO"))
    assert s["label"] == "Springfield · Lucha libre spot opens"
    assert all("value" not in o for o in s["outputs"])
