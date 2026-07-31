"""Offline tests for native-vs-imported classification (stubbed API, no network)."""
from onshape.classify import _is_import_type, classify
from onshape.client import PartStudio


class FakeApi:
    def __init__(self, feature_types):
        self._fts = feature_types

    def features(self, ps):
        return {
            "features": [
                {"featureType": ft, "name": f"F{i}", "suppressed": supp}
                for i, (ft, supp) in enumerate(self._fts)
            ]
        }


PS = PartStudio(did="d" * 24, wvm="w", wid="w" * 24, eid="e" * 24, url="u")


def _types(*fts):
    return [(ft, False) for ft in fts]


def test_import_type_matching():
    assert _is_import_type("importForeign")
    assert _is_import_type("importDerived")
    assert _is_import_type("import")
    assert _is_import_type("derived")
    assert not _is_import_type("extrude")
    assert not _is_import_type("newSketch")
    assert not _is_import_type("fillet")


def test_native_washer_like():
    api = FakeApi(_types("newSketch", "extrude", "circularPattern", "booleanBodies", "fillet"))
    c = classify(PS, api)
    assert c.native is True
    assert c.import_features == []
    assert c.feature_count == 5


def test_imported_gear_like():
    api = FakeApi(_types("importForeign", "newSketch", "extrude", "chamfer"))
    c = classify(PS, api)
    assert c.native is False
    assert "importForeign" in c.reason
    assert len(c.import_features) == 1


def test_suppressed_import_is_inert():
    api = FakeApi([("importForeign", True), ("newSketch", False), ("extrude", False)])
    c = classify(PS, api)
    assert c.native is True
    assert "suppressed" in c.reason
    assert len(c.import_features) == 1  # still reported, just not disqualifying
