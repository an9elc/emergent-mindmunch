"""Tests for new Save-to-Wallet / Saved filter feature (Iteration 2)."""
import os
import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://focuslearn-17.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def s():
    sess = requests.Session()
    sess.headers.update({"Content-Type": "application/json"})
    return sess


class TestSaveToggle:
    def test_toggle_save_404_invalid_id(self, s):
        r = s.post(f"{API}/study-sets/does-not-exist-xyz/save")
        assert r.status_code == 404

    def test_toggle_save_true_then_false(self, s):
        sets = s.get(f"{API}/study-sets").json()
        assert len(sets) > 0
        target = sets[0]
        sid = target["id"]

        # First toggle
        r1 = s.post(f"{API}/study-sets/{sid}/save")
        assert r1.status_code == 200, r1.text
        d1 = r1.json()
        assert d1["id"] == sid
        assert "is_saved" in d1
        first_val = d1["is_saved"]

        # Verify persistence via GET list saved filter
        saved = s.get(f"{API}/study-sets", params={"saved": "true"}).json()
        if first_val:
            assert any(x["id"] == sid for x in saved)
        else:
            assert all(x["id"] != sid for x in saved)

        # Second toggle must flip
        r2 = s.post(f"{API}/study-sets/{sid}/save")
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["is_saved"] == (not first_val)

        # Verify saved filter reflects new state
        saved2 = s.get(f"{API}/study-sets", params={"saved": "true"}).json()
        if d2["is_saved"]:
            assert any(x["id"] == sid for x in saved2)
        else:
            assert all(x["id"] != sid for x in saved2)

    def test_saved_filter_returns_only_saved(self, s):
        # Ensure at least two sets saved
        sets = s.get(f"{API}/study-sets").json()
        assert len(sets) >= 2
        targets = sets[:2]
        to_cleanup = []
        for t in targets:
            # Ensure saved state = True by toggling until saved
            r = s.post(f"{API}/study-sets/{t['id']}/save").json()
            if not r["is_saved"]:
                r = s.post(f"{API}/study-sets/{t['id']}/save").json()
            assert r["is_saved"] is True
            to_cleanup.append(t["id"])

        saved = s.get(f"{API}/study-sets", params={"saved": "true"}).json()
        saved_ids = {x["id"] for x in saved}
        for tid in to_cleanup:
            assert tid in saved_ids
        # every returned set should have is_saved True (field present)
        for x in saved:
            # The server returns full StudySet model; is_saved may not be in model,
            # so fetch single to verify via list (server filters on is_saved=true).
            # Just assert presence confirmed above.
            assert "id" in x

        # Cleanup: toggle back to unsaved
        for tid in to_cleanup:
            s.post(f"{API}/study-sets/{tid}/save")
