"""FocusLearn backend API tests."""
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


# -------- Basic --------
class TestHealth:
    def test_root(self, s):
        r = s.get(f"{API}/")
        assert r.status_code == 200
        assert r.json().get("message") == "FocusLearn API"

    def test_profile_default(self, s):
        r = s.get(f"{API}/profile")
        assert r.status_code == 200
        d = r.json()
        assert d["id"] == "default"
        assert isinstance(d["points"], int)


# -------- Screen time --------
class TestScreenTime:
    @pytest.mark.parametrize("period", ["daily", "weekly", "monthly"])
    def test_screen_time_periods(self, s, period):
        r = s.get(f"{API}/screen-time", params={"period": period})
        assert r.status_code == 200
        d = r.json()
        assert d["period"] == period
        assert len(d["labels"]) == len(d["values"])
        assert d["total_minutes"] == sum(d["values"])


# -------- Subjects + Study sets --------
class TestStudySets:
    def test_subjects_has_all(self, s):
        r = s.get(f"{API}/subjects")
        assert r.status_code == 200
        subs = r.json()["subjects"]
        assert "All" in subs
        assert len(subs) >= 9

    def test_list_study_sets_seeded(self, s):
        r = s.get(f"{API}/study-sets")
        assert r.status_code == 200
        sets = r.json()
        assert len(sets) >= 10
        # each should have cards
        for st in sets[:3]:
            assert "id" in st and "title" in st and "cards" in st

    def test_filter_by_subject(self, s):
        r = s.get(f"{API}/study-sets", params={"subject": "Biology"})
        assert r.status_code == 200
        sets = r.json()
        assert all(x["subject"] == "Biology" for x in sets)
        assert len(sets) >= 1

    def test_filter_by_query(self, s):
        r = s.get(f"{API}/study-sets", params={"query": "Calculus"})
        assert r.status_code == 200
        assert any("Calculus" in x["title"] for x in r.json())

    def test_get_single_study_set(self, s):
        sets = s.get(f"{API}/study-sets").json()
        sid = sets[0]["id"]
        r = s.get(f"{API}/study-sets/{sid}")
        assert r.status_code == 200
        assert r.json()["id"] == sid

    def test_get_missing_study_set_404(self, s):
        r = s.get(f"{API}/study-sets/does-not-exist")
        assert r.status_code == 404

    def test_create_study_set(self, s):
        payload = {
            "title": "TEST_Custom Set",
            "subject": "Biology",
            "description": "test",
            "cover_emoji": "🧪",
            "cards": [
                {"term": "A", "definition": "First letter"},
                {"term": "B", "definition": "Second letter"},
            ],
        }
        r = s.post(f"{API}/study-sets", json=payload)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["is_mine"] is True
        assert d["author"] == "You"
        # verify persistence
        rg = s.get(f"{API}/study-sets/{d['id']}")
        assert rg.status_code == 200
        assert rg.json()["title"] == "TEST_Custom Set"


# -------- Blocked apps --------
class TestBlockedApps:
    def test_list_seeded(self, s):
        r = s.get(f"{API}/blocked-apps")
        assert r.status_code == 200
        apps = r.json()
        names = [a["name"] for a in apps]
        for expected in ["Instagram", "TikTok"]:
            assert expected in names

    def test_create_and_delete(self, s):
        r = s.post(f"{API}/blocked-apps", json={"name": "TEST_App", "icon": "🧪", "daily_limit_minutes": 45})
        assert r.status_code == 200
        app = r.json()
        assert app["name"] == "TEST_App"
        # delete
        rd = s.delete(f"{API}/blocked-apps/{app['id']}")
        assert rd.status_code == 200
        assert rd.json()["deleted"] == 1


# -------- Quiz flow + redeem --------
class TestQuizAndRedeem:
    def test_full_quiz_and_redeem_flow(self, s):
        # Capture starting profile points
        start_profile = s.get(f"{API}/profile").json()
        start_points = start_profile["points"]

        # Pick a seeded study set with cards
        sets = s.get(f"{API}/study-sets").json()
        target = next((x for x in sets if len(x["cards"]) >= 5 and x["is_public"]), None)
        assert target is not None

        # Start
        r = s.post(f"{API}/quiz-sessions/start", json={
            "study_set_id": target["id"], "num_questions": 5,
            "target_accuracy": 0.8, "mode": "practice",
        })
        assert r.status_code == 200, r.text
        session = r.json()
        assert len(session["questions"]) == 5
        for q in session["questions"]:
            assert len(q["options"]) == 4
            assert 0 <= q["correct_index"] <= 3
        sid = session["id"]

        # Answer all correctly
        for i, q in enumerate(session["questions"]):
            ar = s.post(f"{API}/quiz-sessions/{sid}/answer",
                        json={"question_index": i, "answer_index": q["correct_index"]})
            assert ar.status_code == 200
            assert ar.json()["is_correct"] is True

        # Complete
        cr = s.post(f"{API}/quiz-sessions/{sid}/complete")
        assert cr.status_code == 200
        cd = cr.json()
        assert cd["correct"] == 5
        assert cd["passed"] is True
        # practice: 5 correct * 7 + 38 pass bonus = 73
        assert cd["points_earned"] == 73

        # Verify profile updated
        after = s.get(f"{API}/profile").json()
        assert after["points"] == start_points + 73
        assert after["total_earned"] >= start_profile["total_earned"] + 73

    def test_redeem_invalid_multiples(self, s):
        r = s.post(f"{API}/rewards/redeem", json={"points": 100})
        assert r.status_code == 400
        r = s.post(f"{API}/rewards/redeem", json={"points": 151})
        assert r.status_code == 400

    def test_redeem_insufficient(self, s):
        # Profile likely <150 unless many runs
        profile = s.get(f"{API}/profile").json()
        if profile["points"] < 150:
            r = s.post(f"{API}/rewards/redeem", json={"points": 150})
            assert r.status_code == 400

    def test_redeem_success_when_enough(self, s):
        # Run quizzes until >= 150 points (each full-correct gives 50)
        for _ in range(3):
            profile = s.get(f"{API}/profile").json()
            if profile["points"] >= 150:
                break
            sets = s.get(f"{API}/study-sets").json()
            target = next((x for x in sets if len(x["cards"]) >= 5 and x["is_public"]), None)
            session = s.post(f"{API}/quiz-sessions/start", json={
                "study_set_id": target["id"], "num_questions": 5,
                "target_accuracy": 0.8, "mode": "practice",
            }).json()
            for i, q in enumerate(session["questions"]):
                s.post(f"{API}/quiz-sessions/{session['id']}/answer",
                       json={"question_index": i, "answer_index": q["correct_index"]})
            s.post(f"{API}/quiz-sessions/{session['id']}/complete")

        profile = s.get(f"{API}/profile").json()
        if profile["points"] >= 150:
            before_pts = profile["points"]
            before_red = profile["screen_time_redeemed_minutes"]
            r = s.post(f"{API}/rewards/redeem", json={"points": 150})
            assert r.status_code == 200, r.text
            d = r.json()
            assert d["minutes_added"] == 15
            after = s.get(f"{API}/profile").json()
            assert after["points"] == before_pts - 150
            assert after["screen_time_redeemed_minutes"] == before_red + 15
        else:
            pytest.skip("Could not accrue 150 points")
