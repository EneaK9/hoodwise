from ingestion.hyundai import catalog_targets, doc_id_for, load_catalog, parse_years, powertrain_label


def test_year_ranges() -> None:
    assert parse_years("2016-2018") == (2016, 2018)
    assert parse_years("2024-present", now=2026) == (2024, 2026)
    assert parse_years("SANTA FE 2016-2018") == (2016, 2018)


def test_doc_ids_are_unique() -> None:
    ids = [doc_id_for(item) for item in load_catalog()["manuals"]]
    assert len(ids) == len(set(ids))
    assert all(doc_id.startswith("HY_") for doc_id in ids)


def test_powertrain_labels() -> None:
    assert powertrain_label("SANTA FE Hybrid 2019-2023") == "Hybrid"
    assert powertrain_label("SANTA FE Plug-in Hybrid 2019-2023") == "PHEV"
    assert powertrain_label("KONA Electric 2020-2023") == "Electric"


def test_catalog_resolves_downloaded_pdfs() -> None:
    targets = catalog_targets()
    assert len(targets) >= 50
    assert any("2016" in meta["label"] and "Santa" in meta["model"] for _, meta in targets)
