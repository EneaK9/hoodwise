from pathlib import Path

from ingestion.catalog import (
    catalog_targets,
    describe_item,
    describe_pdf,
    doc_id_for,
    load_catalogs,
    make_prefix,
    parse_years,
    powertrain_label,
)


def test_year_ranges() -> None:
    assert parse_years("2016-2018") == (2016, 2018)
    assert parse_years("2024-present", now=2026) == (2024, 2026)
    assert parse_years("SANTA FE 2016-2018") == (2016, 2018)


def test_make_prefix_is_generic() -> None:
    assert make_prefix("Hyundai") == "HY"
    assert make_prefix("Toyota") == "TO"
    assert make_prefix("Honda") == "HO"


def test_doc_ids_are_unique() -> None:
    catalog = load_catalogs()[0]
    items = [{**item, "make": catalog["make"]} for item in catalog["manuals"]]
    ids = [doc_id_for(item) for item in items]
    assert len(ids) == len(set(ids))
    assert all(doc_id.startswith(make_prefix(catalog["make"]) + "_") for doc_id in ids)


def test_describe_item_uses_catalog_make() -> None:
    catalog = {"make": "Toyota", "kind": "owners_manual", "chassis": "OM", "market": "UK"}
    meta = describe_item({"model": "Corolla", "label": "2019-2022", "url": "http://x/corollapdf"}, catalog)
    assert meta["make"] == "Toyota"
    assert meta["doc_id"].startswith("TO_")
    assert meta["section_name"].startswith("Toyota Corolla")


def test_powertrain_labels() -> None:
    assert powertrain_label("SANTA FE Hybrid 2019-2023") == "Hybrid"
    assert powertrain_label("SANTA FE Plug-in Hybrid 2019-2023") == "PHEV"
    assert powertrain_label("KONA Electric 2020-2023") == "Electric"


def test_catalog_resolves_downloaded_pdfs() -> None:
    targets = catalog_targets()
    assert len(targets) >= 50
    assert any("2016" in meta["label"] and "Santa" in meta["model"] for _, meta in targets)
    assert all(meta["make"] for _, meta in targets)


def test_describe_pdf_does_not_collapse_avn_into_owner_manual() -> None:
    targets = {meta["doc_id"]: path for path, meta in catalog_targets()}
    avn_id = next(doc_id for doc_id in targets if "i40" in doc_id and "Audio" in doc_id)
    main_id = next(doc_id for doc_id in targets if doc_id.endswith("_i40") or "2011_2019_i40" in doc_id and "Audio" not in doc_id)
    avn = describe_pdf(targets[avn_id])
    main = describe_pdf(targets[main_id])
    assert avn is not None and main is not None
    assert avn["doc_id"] != main["doc_id"]
    assert "Audio" in avn["label"] or "AVN" in avn["doc_id"]
