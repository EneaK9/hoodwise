from app.snippets import pages_from_answer, snippet_needles


def test_pages_from_answer() -> None:
    assert pages_from_answer("see pages 760, 762") == [760, 762]
    assert pages_from_answer("*(doc: HY_X, pages 760, 762)*") == [760, 762]
    assert pages_from_answer("p.792") == [792]


def test_needles_keep_fog() -> None:
    words = [w.lower() for w in snippet_needles("what fog bulb does this take", "")]
    assert "fog" in words
    assert "bulb" in words
