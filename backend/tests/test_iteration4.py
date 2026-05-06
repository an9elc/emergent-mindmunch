"""Iteration 4 new feature tests: profile update, flashcard sessions, quiz modes, leaderboard."""
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


# -------- Profile username + referral + level/xp --------
class TestProfileIter4:
    def test_profile_has_new_fields(self, s):
        r = s.get(f"{API}/profile")
        assert r.status_code == 200
        d = r.json()
        for field in ("username", "referral_code", "level", "xp_to_next", "points", "total_earned"):
            assert field in d, f"missing {field}"
        assert isinstance(d["username"], str) and len(d["username"]) > 0
        assert isinstance(d["referral_code"], str) and d["referral_code"].startswith("FOCUS-")
        assert isinstance(d["level"], int) and d["level"] >= 1
        assert isinstance(d["xp_to_next"], int) and 0 < d["xp_to_next"] <= 100

    def test_patch_profile_username(self, s):
        new_name = "Alex"
        r = s.patch(f"{API}/profile", json={"username": new_name})
        assert r.status_code == 200, r.text
        assert r.json()["username"] == new_name
        # verify persistence via GET
        g = s.get(f"{API}/profile").json()
        assert g["username"] == new_name

    def test_patch_profile_ignores_empty(self, s):
        # set to a known value first
        s.patch(f"{API}/profile", json={"username": "StableName"})
        r = s.patch(f"{API}/profile", json={"username": "   "})
        assert r.status_code == 200
        assert r.json()["username"] == "StableName"

    def test_patch_profile_truncates_long(self, s):
        long_name = "A" * 50
        r = s.patch(f"{API}/profile", json={"username": long_name})
        assert r.status_code == 200
        assert len(r.json()["username"]) <= 24


# -------- Flashcard sessions --------
class TestFlashcardSessions:
    @pytest.fixture(scope="class")
    def study_set(self, s):
        sets = s.get(f"{API}/study-sets").json()
        target = next(x for x in sets if len(x["cards"]) >= 3)
        return target

    def test_start_session_returns_queue(self, s, study_set):
        r = s.post(f"{API}/flashcard-sessions/start", json={"study_set_id": study_set["id"]})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["study_set_id"] == study_set["id"]
        assert len(d["queue"]) == len(study_set["cards"])
        assert d["got_it_ids"] == []
        assert d["completed"] is False

    def test_start_invalid_set(self, s):
        r = s.post(f"{API}/flashcard-sessions/start", json={"study_set_id": "nope"})
        assert r.status_code == 404

    def test_got_it_removes_card(self, s, study_set):
        session = s.post(f"{API}/flashcard-sessions/start", json={"study_set_id": study_set["id"]}).json()
        sid = session["id"]
        card_id = session["queue"][0]
        initial_len = len(session["queue"])
        r = s.post(f"{API}/flashcard-sessions/{sid}/review", json={"card_id": card_id, "outcome": "got_it"})
        assert r.status_code == 200
        d = r.json()
        assert d["queue_length"] == initial_len - 1
        assert d["got_it_count"] == 1
        # verify via GET
        g = s.get(f"{API}/flashcard-sessions/{sid}").json()
        assert card_id not in g["queue"]
        assert card_id in g["got_it_ids"]

    def test_more_practice_requeues_middle(self, s, study_set):
        session = s.post(f"{API}/flashcard-sessions/start", json={"study_set_id": study_set["id"]}).json()
        sid = session["id"]
        card_id = session["queue"][0]
        initial_len = len(session["queue"])
        r = s.post(f"{API}/flashcard-sessions/{sid}/review", json={"card_id": card_id, "outcome": "more_practice"})
        assert r.status_code == 200
        assert r.json()["queue_length"] == initial_len  # removed+re-added
        g = s.get(f"{API}/flashcard-sessions/{sid}").json()
        assert card_id in g["queue"]

    def test_not_quite_requeues_front(self, s, study_set):
        session = s.post(f"{API}/flashcard-sessions/start", json={"study_set_id": study_set["id"]}).json()
        sid = session["id"]
        card_id = session["queue"][0]
        r = s.post(f"{API}/flashcard-sessions/{sid}/review", json={"card_id": card_id, "outcome": "not_quite"})
        assert r.status_code == 200
        g = s.get(f"{API}/flashcard-sessions/{sid}").json()
        # card should appear within the first 3 positions (near front)
        assert card_id in g["queue"][:3]

    def test_invalid_outcome_400(self, s, study_set):
        session = s.post(f"{API}/flashcard-sessions/start", json={"study_set_id": study_set["id"]}).json()
        sid = session["id"]
        card_id = session["queue"][0]
        r = s.post(f"{API}/flashcard-sessions/{sid}/review", json={"card_id": card_id, "outcome": "bogus"})
        assert r.status_code == 400

    def test_complete_awards_3_per_got_it(self, s, study_set):
        # Start a fresh session, mark 3 cards as got_it
        session = s.post(f"{API}/flashcard-sessions/start", json={"study_set_id": study_set["id"]}).json()
        sid = session["id"]
        got_it_cards = session["queue"][:3]

        before_profile = s.get(f"{API}/profile").json()
        before_pts = before_profile["points"]
        before_total = before_profile["total_earned"]

        for cid in got_it_cards:
            r = s.post(f"{API}/flashcard-sessions/{sid}/review", json={"card_id": cid, "outcome": "got_it"})
            assert r.status_code == 200

        r = s.post(f"{API}/flashcard-sessions/{sid}/complete")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["got_it_count"] == 3
        assert d["points_earned"] == 9  # 3 * 3

        after = s.get(f"{API}/profile").json()
        assert after["points"] == before_pts + 9
        assert after["total_earned"] == before_total + 9


# -------- Quiz mode point differentiation --------
class TestQuizModePoints:
    def _run_full_correct_quiz(self, s, mode):
        sets = s.get(f"{API}/study-sets").json()
        target = next(x for x in sets if len(x["cards"]) >= 5)
        session = s.post(f"{API}/quiz-sessions/start", json={
            "study_set_id": target["id"], "num_questions": 5,
            "target_accuracy": 0.8, "mode": mode,
        }).json()
        sid = session["id"]
        for i, q in enumerate(session["questions"]):
            s.post(f"{API}/quiz-sessions/{sid}/answer",
                   json={"question_index": i, "answer_index": q["correct_index"]})
        r = s.post(f"{API}/quiz-sessions/{sid}/complete")
        assert r.status_code == 200, r.text
        return r.json()

    def test_practice_mode_points(self, s):
        d = self._run_full_correct_quiz(s, "practice")
        assert d["mode"] == "practice"
        assert d["passed"] is True
        # 5 * 7 + 38 = 73
        assert d["points_earned"] == 73

    def test_lock_challenge_mode_points(self, s):
        d = self._run_full_correct_quiz(s, "lock_challenge")
        assert d["mode"] == "lock_challenge"
        assert d["passed"] is True
        # 5 * 5 + 25 = 50
        assert d["points_earned"] == 50


# -------- Social leaderboard --------
class TestLeaderboard:
    def test_leaderboard_structure(self, s):
        r = s.get(f"{API}/social/leaderboard")
        assert r.status_code == 200
        d = r.json()
        assert "friends" in d
        friends = d["friends"]
        assert len(friends) >= 5  # 4 seeded + me
        names = [f["name"] for f in friends]
        for seeded in ("Maya", "Jordan", "Sam", "Riley"):
            assert seeded in names, f"missing seeded friend {seeded}"

    def test_leaderboard_sorted_desc(self, s):
        friends = s.get(f"{API}/social/leaderboard").json()["friends"]
        pts = [f["points"] for f in friends]
        assert pts == sorted(pts, reverse=True)

    def test_leaderboard_has_exactly_one_me(self, s):
        friends = s.get(f"{API}/social/leaderboard").json()["friends"]
        me_rows = [f for f in friends if f.get("is_me") is True]
        assert len(me_rows) == 1
        me = me_rows[0]
        for field in ("name", "avatar", "points", "level"):
            assert field in me
