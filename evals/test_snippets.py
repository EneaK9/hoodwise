from app.snippets import pages_from_answer, snippet_needles


def test_pages_from_answer() -> None:
    assert pages_from_answer("see pages 760, 762") == [760, 762]
    assert pages_from_answer("*(doc: HY_X, pages 760, 762)*") == [760, 762]
    assert pages_from_answer("p.792") == [792]


def test_needles_highlight_the_quoted_figures_first() -> None:
    needles = snippet_needles("how much engine oil does the diesel take", "It takes 6.3 l (6.66 US qt.) (p.792)")
    assert needles[:3] == ["6.3", "6.66", "792"]
    lowered = [n.lower() for n in needles]
    assert "engine" in lowered and "diesel" in lowered
    assert len(needles) <= 8
