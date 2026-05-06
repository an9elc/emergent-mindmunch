from fastapi import FastAPI, APIRouter, HTTPException
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import random
import json
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime, timezone, timedelta

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

app = FastAPI()
api_router = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# -------- Models --------
class Flashcard(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    term: str
    definition: str

class StudySet(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    subject: str
    description: str = ""
    author: str = "Community"
    is_public: bool = True
    is_mine: bool = False
    is_saved: bool = False
    cover_emoji: str = "📘"
    cards: List[Flashcard] = []
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class StudySetCreate(BaseModel):
    title: str
    subject: str
    description: str = ""
    cover_emoji: str = "📘"
    cards: List[Flashcard] = []

class BlockedApp(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    icon: str = "📱"
    daily_limit_minutes: int = 60
    used_minutes: int = 0
    is_blocked: bool = True
    required_accuracy: float = 0.8  # 0.5–1.0; how well the user must do on the unlock quiz
    unlock_questions: int = 5       # how many questions the user must answer to unlock

class BlockedAppCreate(BaseModel):
    name: str
    icon: str = "📱"
    daily_limit_minutes: int = 60
    required_accuracy: float = 0.8
    unlock_questions: int = 5

class Profile(BaseModel):
    id: str = "default"
    username: str = "Friend"
    referral_code: str = ""
    points: int = 0
    total_earned: int = 0
    screen_time_redeemed_minutes: int = 0
    streak_days: int = 0
    level: int = 1
    xp_to_next: int = 100
    is_onboarded: bool = False
    age_range: str = ""
    subject_interest: str = ""
    daily_screentime_estimate_minutes: int = 0
    learning_level: str = ""

class QuizQuestion(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    question: str
    question_type: str = "mcq"  # mcq | free_response
    options: List[str] = []
    correct_index: int = 0
    correct_answer: str = ""  # used for free_response; also acceptable alternatives via '|' split
    explanation: str = ""

class QuizSession(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    study_set_id: str
    questions: List[QuizQuestion]
    answers: List[int] = []
    target_accuracy: float = 0.8
    required_correct: int = 4
    mode: str = "practice"  # practice | lock_challenge
    completed: bool = False
    passed: bool = False
    points_earned: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class QuizStartRequest(BaseModel):
    study_set_id: str
    num_questions: int = 5
    target_accuracy: float = 0.8
    mode: str = "practice"

class QuizAnswerRequest(BaseModel):
    question_index: int
    answer_index: Optional[int] = None
    answer_text: Optional[str] = None

class RedeemRequest(BaseModel):
    points: int
    app_id: Optional[str] = None  # if provided, extra minutes are granted to this specific app

# -------- Helpers --------
def clean(doc):
    if not doc:
        return doc
    doc.pop('_id', None)
    return doc

async def get_or_create_profile() -> Profile:
    doc = await db.profile.find_one({"id": "default"}, {"_id": 0})
    if not doc:
        import secrets
        p = Profile(referral_code=f"FOCUS-{secrets.token_hex(3).upper()}")
        await db.profile.insert_one(p.model_dump())
        return p
    # backfill missing fields
    updates = {}
    if not doc.get("username"): updates["username"] = "Friend"
    if not doc.get("referral_code"):
        import secrets
        updates["referral_code"] = f"FOCUS-{secrets.token_hex(3).upper()}"
    # Derive level from total_earned (100 XP per level)
    total = int(doc.get("total_earned", 0))
    lvl = 1 + total // 100
    next_xp = 100 - (total % 100)
    updates["level"] = lvl
    updates["xp_to_next"] = next_xp
    if updates:
        await db.profile.update_one({"id": "default"}, {"$set": updates})
        doc.update(updates)
    return Profile(**{k: v for k, v in doc.items() if k in Profile.model_fields})

# -------- Routes --------
@api_router.get("/")
async def root():
    return {"message": "FocusLearn API"}

@api_router.get("/profile", response_model=Profile)
async def get_profile():
    return await get_or_create_profile()

class ProfileUpdate(BaseModel):
    username: Optional[str] = None

class OnboardingComplete(BaseModel):
    age_range: str
    subject_interest: str
    daily_screentime_estimate_minutes: int
    username: Optional[str] = None
    bonus_points: int = 50
    learning_level: Optional[str] = None

@api_router.patch("/profile", response_model=Profile)
async def update_profile(inp: ProfileUpdate):
    updates: Dict[str, Any] = {}
    if inp.username is not None and inp.username.strip():
        updates["username"] = inp.username.strip()[:24]
    if updates:
        await db.profile.update_one({"id": "default"}, {"$set": updates}, upsert=True)
    return await get_or_create_profile()

@api_router.post("/onboarding/reset", response_model=Profile)
async def reset_onboarding():
    """Dev-friendly endpoint to flip is_onboarded back to false so the flow can be replayed."""
    await db.profile.update_one({"id": "default"}, {"$set": {"is_onboarded": False}}, upsert=True)
    return await get_or_create_profile()

@api_router.post("/onboarding/complete", response_model=Profile)
async def complete_onboarding(inp: OnboardingComplete):
    """Marks the user as onboarded, stores intake answers, and grants bonus bytes for finishing the tutorial quiz."""
    p = await get_or_create_profile()
    bonus = max(0, min(200, int(inp.bonus_points)))
    new_points = int(p.points) + bonus
    new_total = int(p.total_earned) + bonus
    updates: Dict[str, Any] = {
        "is_onboarded": True,
        "age_range": inp.age_range[:16],
        "subject_interest": inp.subject_interest[:48],
        "daily_screentime_estimate_minutes": max(0, min(1440, int(inp.daily_screentime_estimate_minutes))),
        "points": new_points,
        "total_earned": new_total,
        "streak_days": max(int(p.streak_days), 1),
    }
    if inp.username and inp.username.strip():
        updates["username"] = inp.username.strip()[:24]
    if inp.learning_level:
        updates["learning_level"] = inp.learning_level[:32]
    await db.profile.update_one({"id": "default"}, {"$set": updates}, upsert=True)
    return await get_or_create_profile()

# ---- Screen time mock ----
@api_router.get("/screen-time")
async def screen_time(period: str = "daily"):
    random.seed(hash(period))
    if period == "daily":
        labels = ["12a", "4a", "8a", "12p", "4p", "8p"]
        values = [random.randint(5, 45) for _ in labels]
        total_minutes = sum(values)
        unit = "min"
    elif period == "weekly":
        labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        values = [random.randint(60, 320) for _ in labels]
        total_minutes = sum(values)
        unit = "min"
    else:
        labels = ["W1", "W2", "W3", "W4"]
        values = [random.randint(800, 2000) for _ in labels]
        total_minutes = sum(values)
        unit = "min"
    return {
        "period": period,
        "labels": labels,
        "values": values,
        "total_minutes": total_minutes,
        "unit": unit,
        "average": total_minutes // max(len(values), 1),
    }

# ---- Blocked apps ----
@api_router.get("/blocked-apps", response_model=List[BlockedApp])
async def list_blocked_apps():
    docs = await db.blocked_apps.find({}, {"_id": 0}).to_list(200)
    return [BlockedApp(**d) for d in docs]

@api_router.post("/blocked-apps", response_model=BlockedApp)
async def create_blocked_app(inp: BlockedAppCreate):
    # Idempotent by name (case-insensitive trim) — prevents duplicate apps from accumulating
    # across replays of onboarding or repeated submissions.
    norm = inp.name.strip()
    existing = await db.blocked_apps.find_one(
        {"name": {"$regex": f"^{norm}$", "$options": "i"}}, {"_id": 0}
    )
    if existing:
        # Update settings on the existing record rather than creating a new one.
        updates = {
            "icon": inp.icon,
            "daily_limit_minutes": inp.daily_limit_minutes,
            "required_accuracy": inp.required_accuracy,
            "unlock_questions": inp.unlock_questions,
            "is_blocked": True,
        }
        await db.blocked_apps.update_one({"id": existing["id"]}, {"$set": updates})
        existing.update(updates)
        return BlockedApp(**existing)
    obj = BlockedApp(**inp.model_dump(), used_minutes=random.randint(0, inp.daily_limit_minutes))
    await db.blocked_apps.insert_one(obj.model_dump())
    return obj

@api_router.delete("/blocked-apps/{app_id}")
async def delete_blocked_app(app_id: str):
    res = await db.blocked_apps.delete_one({"id": app_id})
    return {"deleted": res.deleted_count}

# ---- Study sets ----
@api_router.get("/study-sets", response_model=List[StudySet])
async def list_study_sets(subject: Optional[str] = None, query: Optional[str] = None, mine: Optional[bool] = None, saved: Optional[bool] = None):
    filt: Dict[str, Any] = {}
    if subject and subject != "All":
        filt["subject"] = subject
    if mine is True:
        filt["is_mine"] = True
    if saved is True:
        filt["is_saved"] = True
    if query:
        filt["title"] = {"$regex": query, "$options": "i"}
    docs = await db.study_sets.find(filt, {"_id": 0}).sort("created_at", -1).to_list(500)
    return [StudySet(**d) for d in docs]

@api_router.get("/study-sets/{set_id}", response_model=StudySet)
async def get_study_set(set_id: str):
    doc = await db.study_sets.find_one({"id": set_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Study set not found")
    return StudySet(**doc)

@api_router.post("/study-sets", response_model=StudySet)
async def create_study_set(inp: StudySetCreate):
    obj = StudySet(**inp.model_dump(), author="You", is_mine=True, is_public=False)
    await db.study_sets.insert_one(obj.model_dump())
    return obj

@api_router.get("/subjects")
async def list_subjects():
    subjects = await db.study_sets.distinct("subject")
    return {"subjects": ["All"] + sorted(subjects)}

@api_router.post("/study-sets/{set_id}/save")
async def toggle_save(set_id: str):
    doc = await db.study_sets.find_one({"id": set_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Study set not found")
    new_val = not bool(doc.get("is_saved", False))
    await db.study_sets.update_one({"id": set_id}, {"$set": {"is_saved": new_val}})
    return {"id": set_id, "is_saved": new_val}

# ---- AI quiz generation ----
async def generate_quiz_questions(study_set: StudySet, num: int) -> List[QuizQuestion]:
    # Build a deterministic base from flashcards, optionally enhanced with AI
    cards = study_set.cards[:]
    if not cards:
        return []

    ai_questions: List[QuizQuestion] = []
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if api_key and len(cards) >= 2:
        try:
            from emergentintegrations.llm.chat import LlmChat, UserMessage
            chat = LlmChat(
                api_key=api_key,
                session_id=f"quizgen-{study_set.id}-{uuid.uuid4()}",
                system_message=(
                    "You are an academic quiz generator. Create multiple-choice quiz "
                    "questions based on provided flashcards. Return ONLY valid JSON, no prose."
                ),
            ).with_model("openai", "gpt-5.2")
            card_json = json.dumps([{"term": c.term, "definition": c.definition} for c in cards[:12]])
            prompt = (
                f"Generate {num} questions from these flashcards for the subject '{study_set.subject}'. "
                f"Mix types: about 75% multiple-choice (4 options each) and 25% short free-response (single-word or brief term). "
                f"Flashcards JSON: {card_json}. "
                "Return JSON array with objects. For multiple choice use: "
                '{"question": str, "type": "mcq", "options": [str,str,str,str], "correct_index": int, "explanation": str}. '
                "For free response use: "
                '{"question": str, "type": "free_response", "correct_answer": str, "explanation": str}. '
                "Free-response answers must be short (1-4 words). Output ONLY the JSON array."
            )
            resp = await chat.send_message(UserMessage(text=prompt))
            text = resp.strip()
            if text.startswith("```"):
                text = text.strip("`")
                if text.lower().startswith("json"):
                    text = text[4:]
            # Try to extract JSON array
            start = text.find("[")
            end = text.rfind("]")
            if start != -1 and end != -1:
                data = json.loads(text[start:end+1])
                for q in data[:num]:
                    if not isinstance(q, dict):
                        continue
                    qtype = str(q.get("type", "mcq")).lower()
                    if qtype == "free_response" and q.get("correct_answer"):
                        ai_questions.append(QuizQuestion(
                            question=str(q["question"]),
                            question_type="free_response",
                            options=[],
                            correct_index=0,
                            correct_answer=str(q["correct_answer"]),
                            explanation=str(q.get("explanation", "")),
                        ))
                    elif (isinstance(q.get("options"), list)
                            and len(q["options"]) == 4 and isinstance(q.get("correct_index"), int)
                            and 0 <= q["correct_index"] <= 3):
                        ai_questions.append(QuizQuestion(
                            question=str(q["question"]),
                            question_type="mcq",
                            options=[str(o) for o in q["options"]],
                            correct_index=int(q["correct_index"]),
                            correct_answer="",
                            explanation=str(q.get("explanation", "")),
                        ))
        except Exception as e:
            logger.warning(f"AI quiz generation failed, falling back: {e}")

    # Fallback: build questions from cards. Mix ~25% free-response, ~75% multiple choice.
    fallback: List[QuizQuestion] = []
    pool = cards[:]
    random.shuffle(pool)
    fr_target = max(1, num // 4) if len(pool) >= num else 0
    fr_used = 0
    for idx, c in enumerate(pool):
        # Alternate type: every 4th question is free-response (term-from-definition)
        make_free = (fr_used < fr_target) and (idx % 4 == 0 or (len(fallback) == num - 1 and fr_used == 0))
        if make_free:
            fallback.append(QuizQuestion(
                question=f"{c.definition}\n\nType the term it describes:",
                question_type="free_response",
                options=[],
                correct_index=0,
                correct_answer=c.term,
                explanation=f"{c.term}: {c.definition}",
            ))
            fr_used += 1
        else:
            distractors = [d.definition for d in cards if d.id != c.id][:3]
            while len(distractors) < 3:
                distractors.append("None of the above")
            options = distractors + [c.definition]
            random.shuffle(options)
            correct_idx = options.index(c.definition)
            fallback.append(QuizQuestion(
                question=f"What is the definition of: {c.term}?",
                question_type="mcq",
                options=options,
                correct_index=correct_idx,
                correct_answer="",
                explanation=f"{c.term}: {c.definition}",
            ))
        if len(fallback) >= num:
            break

    # Merge AI + fallback, dedupe by question
    combined: List[QuizQuestion] = []
    seen = set()
    for q in ai_questions + fallback:
        key = q.question.lower().strip()
        if key not in seen:
            seen.add(key)
            combined.append(q)
        if len(combined) >= num:
            break
    return combined[:num]

# ---- Quiz sessions ----
@api_router.post("/quiz-sessions/start", response_model=QuizSession)
async def start_quiz(inp: QuizStartRequest):
    s = await db.study_sets.find_one({"id": inp.study_set_id}, {"_id": 0})
    if not s:
        raise HTTPException(404, "Study set not found")
    study_set = StudySet(**s)
    if not study_set.cards:
        raise HTTPException(400, "Study set has no cards")
    questions = await generate_quiz_questions(study_set, inp.num_questions)
    if not questions:
        raise HTTPException(400, "Could not generate questions")
    required = max(1, int(round(len(questions) * inp.target_accuracy)))
    session = QuizSession(
        study_set_id=inp.study_set_id,
        questions=questions,
        target_accuracy=inp.target_accuracy,
        required_correct=required,
        mode=inp.mode,
    )
    await db.quiz_sessions.insert_one(session.model_dump())
    return session

@api_router.post("/quiz-sessions/{session_id}/answer")
async def answer_quiz(session_id: str, inp: QuizAnswerRequest):
    doc = await db.quiz_sessions.find_one({"id": session_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Session not found")
    session = QuizSession(**doc)
    if inp.question_index >= len(session.questions):
        raise HTTPException(400, "Invalid question index")
    q = session.questions[inp.question_index]
    stored_answer = -1  # sentinel for free-response; track correctness below

    if q.question_type == "free_response":
        user_text = (inp.answer_text or "").strip().lower()
        # Accept pipe-separated alternatives, e.g. "Paris|paris, france"
        accepted = [a.strip().lower() for a in q.correct_answer.split("|") if a.strip()]
        is_correct = bool(user_text) and user_text in accepted
        stored_answer = 0 if is_correct else 1  # 0 = correct, 1 = wrong (stored as int for existing schema)
        # For free_response we set correct_index=0 convention; completion below handles scoring.
        answers = session.answers + [stored_answer]
        await db.quiz_sessions.update_one({"id": session_id}, {"$set": {"answers": answers}})
        return {
            "is_correct": is_correct,
            "correct_index": 0,
            "correct_answer": q.correct_answer.split("|")[0],
            "explanation": q.explanation,
        }

    # Default MCQ
    if inp.answer_index is None:
        raise HTTPException(400, "answer_index required for multiple choice")
    is_correct = inp.answer_index == q.correct_index
    answers = session.answers + [inp.answer_index]
    await db.quiz_sessions.update_one({"id": session_id}, {"$set": {"answers": answers}})
    return {
        "is_correct": is_correct,
        "correct_index": q.correct_index,
        "explanation": q.explanation,
    }

@api_router.post("/quiz-sessions/{session_id}/complete")
async def complete_quiz(session_id: str):
    doc = await db.quiz_sessions.find_one({"id": session_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Session not found")
    session = QuizSession(**doc)

    def _is_correct(i: int, a: int) -> bool:
        if i >= len(session.questions):
            return False
        q = session.questions[i]
        if q.question_type == "free_response":
            # For free_response we stored 0 = correct, 1 = wrong.
            return a == 0
        return a == q.correct_index

    correct = sum(1 for i, a in enumerate(session.answers) if _is_correct(i, a))
    passed = correct >= session.required_correct
    # Practice quiz (voluntary) is worth 1.5× a lock-challenge quiz to incentivize self-directed study.
    if session.mode == "practice":
        pts_per_correct = 7
        pass_bonus = 38
    else:
        pts_per_correct = 5
        pass_bonus = 25
    earned = correct * pts_per_correct + (pass_bonus if passed else 0)
    await db.quiz_sessions.update_one({"id": session_id}, {"$set": {
        "completed": True, "passed": passed, "points_earned": earned,
    }})
    if earned > 0:
        profile = await get_or_create_profile()
        new_points = profile.points + earned
        new_total = profile.total_earned + earned
        await db.profile.update_one({"id": "default"}, {"$set": {
            "points": new_points, "total_earned": new_total,
        }}, upsert=True)
    return {
        "correct": correct,
        "total": len(session.questions),
        "required_correct": session.required_correct,
        "passed": passed,
        "points_earned": earned,
        "mode": session.mode,
    }

# ---- Rewards ----
@api_router.post("/rewards/redeem")
async def redeem(inp: RedeemRequest):
    # 150 points = 15 minutes
    if inp.points < 150 or inp.points % 150 != 0:
        raise HTTPException(400, "Redeem in multiples of 150 points")
    profile = await get_or_create_profile()
    if profile.points < inp.points:
        raise HTTPException(400, "Not enough points")
    minutes = (inp.points // 150) * 15

    # If a target app was selected, grant the extra time directly to that app by
    # reducing its used_minutes by `minutes` (clamped at 0). This effectively gives
    # the user `minutes` more allowance on that specific app for the day.
    granted_to: Optional[str] = None
    if inp.app_id:
        app_doc = await db.blocked_apps.find_one({"id": inp.app_id}, {"_id": 0})
        if not app_doc:
            raise HTTPException(404, "Selected app not found")
        new_used = max(0, int(app_doc.get("used_minutes", 0)) - minutes)
        await db.blocked_apps.update_one(
            {"id": inp.app_id},
            {"$set": {"used_minutes": new_used}},
        )
        granted_to = app_doc.get("name")

    await db.profile.update_one({"id": "default"}, {"$set": {
        "points": profile.points - inp.points,
        "screen_time_redeemed_minutes": profile.screen_time_redeemed_minutes + minutes,
    }})
    return {
        "minutes_added": minutes,
        "remaining_points": profile.points - inp.points,
        "granted_to": granted_to,
    }

# ---- Flashcard sessions (practice flip + self-assess) ----
class FlashcardSession(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    study_set_id: str
    queue: List[str] = []       # card ids remaining
    got_it_ids: List[str] = []
    reviewed: int = 0
    completed: bool = False
    points_earned: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class FlashStart(BaseModel):
    study_set_id: str

class FlashReview(BaseModel):
    card_id: str
    outcome: str  # got_it | more_practice | not_quite

# --- Persistent spaced-repetition (Leitner-style) helpers ---
# Box → next-review interval (in minutes). Box 1 = struggling (immediate), Box 5 = mastered.
SR_INTERVALS_MIN = {1: 0, 2: 60, 3: 1440, 4: 4320, 5: 10080}

async def _upsert_card_review(study_set_id: str, card_id: str, outcome: str) -> None:
    """Upsert per-(user, card) SR record after each flashcard review."""
    now = datetime.now(timezone.utc)
    existing = await db.card_reviews.find_one(
        {"user_id": "default", "card_id": card_id},
        {"_id": 0},
    )
    box = int(existing["box"]) if existing else 1
    times_seen = int(existing.get("times_seen", 0)) if existing else 0
    times_correct = int(existing.get("times_correct", 0)) if existing else 0

    if outcome == "got_it":
        box = min(5, box + 1)
        times_correct += 1
        delta_min = SR_INTERVALS_MIN.get(box, 0)
    elif outcome == "more_practice":
        # Box stays; reappear in ~30 minutes (still within today's session if user keeps going)
        delta_min = 30
    elif outcome == "not_quite":
        box = max(1, box - 1)
        delta_min = 0  # immediately due
    else:
        return  # invalid outcome — don't persist

    times_seen += 1
    due_at = now + timedelta(minutes=delta_min)
    record = {
        "user_id": "default",
        "card_id": card_id,
        "study_set_id": study_set_id,
        "box": box,
        "due_at": due_at,
        "last_outcome": outcome,
        "times_seen": times_seen,
        "times_correct": times_correct,
        "updated_at": now,
    }
    await db.card_reviews.update_one(
        {"user_id": "default", "card_id": card_id},
        {"$set": record},
        upsert=True,
    )

@api_router.post("/flashcard-sessions/start", response_model=FlashcardSession)
async def flash_start(inp: FlashStart):
    s = await db.study_sets.find_one({"id": inp.study_set_id}, {"_id": 0})
    if not s:
        raise HTTPException(404, "Study set not found")
    study_set = StudySet(**s)
    if not study_set.cards:
        raise HTTPException(400, "Study set has no cards")

    # ---- Persistent spaced repetition: order this session's queue by SR state ----
    # Pull existing per-card review records for this user+set.
    now = datetime.now(timezone.utc)
    review_docs = await db.card_reviews.find(
        {"user_id": "default", "study_set_id": inp.study_set_id},
        {"_id": 0},
    ).to_list(1000)
    by_card: Dict[str, Dict[str, Any]] = {r["card_id"]: r for r in review_docs}

    due_cards: List[str] = []        # have a record AND due_at <= now (sorted by due_at asc)
    unseen_cards: List[str] = []     # no record yet
    not_yet_due: List[Dict[str, Any]] = []  # have record but due_at > now (sorted by box asc, then due_at asc)

    for c in study_set.cards:
        rec = by_card.get(c.id)
        if rec is None:
            unseen_cards.append(c.id)
            continue
        due_at = rec.get("due_at")
        # Mongo round-trips datetimes as naive UTC; ensure tz-awareness for compare.
        if isinstance(due_at, datetime) and due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=timezone.utc)
        if due_at is None or due_at <= now:
            due_cards.append((rec.get("due_at") or now, c.id))
        else:
            not_yet_due.append({
                "card_id": c.id,
                "box": int(rec.get("box", 1)),
                "due_at": due_at,
            })

    due_cards.sort(key=lambda t: t[0])         # oldest-overdue first
    due_ids = [cid for _, cid in due_cards]
    not_yet_due.sort(key=lambda r: (r["box"], r["due_at"]))  # most-fragile first
    refresh_ids = [r["card_id"] for r in not_yet_due]

    MAX_SESSION = 15
    queue: List[str] = []
    queue.extend(due_ids)
    if len(queue) < MAX_SESSION:
        random.shuffle(unseen_cards)
        queue.extend(unseen_cards[: MAX_SESSION - len(queue)])
    if len(queue) < MAX_SESSION:
        # Fill remainder with the most-fragile not-yet-due cards (mild "refresh" drill)
        queue.extend(refresh_ids[: MAX_SESSION - len(queue)])
    if not queue:
        # Edge case: somehow nothing — fall back to a random shuffle of all cards
        queue = [c.id for c in study_set.cards]
        random.shuffle(queue)
    queue = queue[:MAX_SESSION]

    session = FlashcardSession(study_set_id=inp.study_set_id, queue=queue)
    await db.flashcard_sessions.insert_one(session.model_dump())
    return session

@api_router.get("/flashcard-sessions/{sid}", response_model=FlashcardSession)
async def flash_get(sid: str):
    doc = await db.flashcard_sessions.find_one({"id": sid}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Session not found")
    return FlashcardSession(**doc)

@api_router.post("/flashcard-sessions/{sid}/review")
async def flash_review(sid: str, inp: FlashReview):
    doc = await db.flashcard_sessions.find_one({"id": sid}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Session not found")
    session = FlashcardSession(**doc)
    queue = list(session.queue)
    got = list(session.got_it_ids)
    if inp.card_id in queue:
        queue.remove(inp.card_id)
    if inp.outcome == "got_it":
        got.append(inp.card_id)
    elif inp.outcome == "more_practice":
        # Re-insert near the middle so it reappears mid-session
        pos = len(queue) // 2
        queue.insert(pos, inp.card_id)
    elif inp.outcome == "not_quite":
        # Re-insert near the front (after ~2 others) so user sees it again soon
        pos = min(2, len(queue))
        queue.insert(pos, inp.card_id)
    else:
        raise HTTPException(400, "Invalid outcome")
    await db.flashcard_sessions.update_one({"id": sid}, {"$set": {
        "queue": queue, "got_it_ids": got, "reviewed": session.reviewed + 1,
    }})
    # Persist per-card SR state across sessions (Leitner box + due_at)
    try:
        await _upsert_card_review(session.study_set_id, inp.card_id, inp.outcome)
    except Exception as e:
        logger.warning(f"Failed to persist card_review: {e}")
    return {"queue_length": len(queue), "reviewed": session.reviewed + 1, "got_it_count": len(got)}

@api_router.post("/flashcard-sessions/{sid}/complete")
async def flash_complete(sid: str):
    doc = await db.flashcard_sessions.find_one({"id": sid}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Session not found")
    session = FlashcardSession(**doc)
    # Flashcard practice = 3 pts per "got it" card (half of the practice quiz per-correct rate).
    earned = 3 * len(session.got_it_ids)
    await db.flashcard_sessions.update_one({"id": sid}, {"$set": {
        "completed": True, "points_earned": earned,
    }})
    if earned > 0:
        profile = await get_or_create_profile()
        await db.profile.update_one({"id": "default"}, {"$set": {
            "points": profile.points + earned,
            "total_earned": profile.total_earned + earned,
        }}, upsert=True)
    return {"points_earned": earned, "got_it_count": len(session.got_it_ids), "reviewed": session.reviewed}

@api_router.get("/study-sets/{set_id}/sr-stats")
async def sr_stats(set_id: str):
    """Returns the persistent SR breakdown for a study set: due_now, mastered, in_progress, never_seen, total."""
    s = await db.study_sets.find_one({"id": set_id}, {"_id": 0})
    if not s:
        raise HTTPException(404, "Study set not found")
    study_set = StudySet(**s)
    total = len(study_set.cards)
    if total == 0:
        return {"total": 0, "due_now": 0, "mastered": 0, "in_progress": 0, "never_seen": 0}

    now = datetime.now(timezone.utc)
    review_docs = await db.card_reviews.find(
        {"user_id": "default", "study_set_id": set_id},
        {"_id": 0},
    ).to_list(1000)
    by_card = {r["card_id"]: r for r in review_docs}

    due_now = 0
    mastered = 0
    in_progress = 0
    never_seen = 0
    for c in study_set.cards:
        rec = by_card.get(c.id)
        if rec is None:
            never_seen += 1
            continue
        box = int(rec.get("box", 1))
        due_at = rec.get("due_at")
        if isinstance(due_at, datetime) and due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=timezone.utc)
        is_due = due_at is None or due_at <= now
        if box >= 5 and not is_due:
            mastered += 1
        elif is_due:
            due_now += 1
        else:
            in_progress += 1
    return {
        "total": total,
        "due_now": due_now,
        "mastered": mastered,
        "in_progress": in_progress,
        "never_seen": never_seen,
    }

# ---- Social leaderboard (seeded friends) ----
SEED_FRIENDS = [
    {"name": "Maya",  "avatar": "🦊", "points": 2480, "level": 24},
    {"name": "Jordan","avatar": "🐼", "points": 1740, "level": 17},
    {"name": "Sam",   "avatar": "🐨", "points":  980, "level":  9},
    {"name": "Riley", "avatar": "🦁", "points":  640, "level":  6},
]

@api_router.get("/social/leaderboard")
async def leaderboard():
    me = await get_or_create_profile()
    friends = [{"name": f["name"], "avatar": f["avatar"], "points": f["points"], "level": f["level"], "is_me": False} for f in SEED_FRIENDS]
    friends.append({"name": me.username or "You", "avatar": "⭐", "points": me.total_earned, "level": me.level, "is_me": True})
    friends.sort(key=lambda x: x["points"], reverse=True)
    return {"friends": friends}

# ---- Seed ----
SEED_STUDY_SETS = [
    {"title": "Biology 101: Cell Basics", "subject": "Biology", "cover_emoji": "🧬", "author": "Dr. Lin",
     "cards": [
        ("Mitochondria", "Organelle that produces ATP energy for the cell."),
        ("Nucleus", "Membrane-bound organelle containing the cell's DNA."),
        ("Ribosome", "Site of protein synthesis in the cell."),
        ("Cell Membrane", "Phospholipid bilayer regulating entry and exit of substances."),
        ("Chloroplast", "Plant organelle where photosynthesis occurs."),
        ("Cytoplasm", "Jelly-like fluid filling the inside of the cell."),
     ]},
    {"title": "Calculus: Derivatives", "subject": "Math", "cover_emoji": "📐", "author": "MathMaven",
     "cards": [
        ("Derivative of x^n", "n * x^(n-1)"),
        ("Derivative of sin(x)", "cos(x)"),
        ("Derivative of cos(x)", "-sin(x)"),
        ("Derivative of e^x", "e^x"),
        ("Derivative of ln(x)", "1/x"),
        ("Product Rule", "(fg)' = f'g + fg'"),
     ]},
    {"title": "US History: Revolution", "subject": "History", "cover_emoji": "🇺🇸", "author": "HistoryBuff",
     "cards": [
        ("1776", "Year the Declaration of Independence was signed."),
        ("Thomas Jefferson", "Primary author of the Declaration of Independence."),
        ("Boston Tea Party", "1773 protest against British tea taxes."),
        ("Yorktown", "1781 battle where Cornwallis surrendered."),
        ("Lexington", "Site of the first shots of the Revolutionary War."),
     ]},
    {"title": "CS: Data Structures", "subject": "Computer Science", "cover_emoji": "💻", "author": "CodeCat",
     "cards": [
        ("Stack", "LIFO data structure supporting push and pop."),
        ("Queue", "FIFO data structure supporting enqueue and dequeue."),
        ("Hash Map", "Key-value store with average O(1) lookup."),
        ("Binary Tree", "Tree where each node has at most two children."),
        ("Linked List", "Linear collection of nodes each pointing to the next."),
        ("Heap", "Complete binary tree satisfying heap-order property."),
     ]},
    {"title": "Spanish Basics", "subject": "Language", "cover_emoji": "🗣️", "author": "PolyGlot",
     "cards": [
        ("Hola", "Hello"),
        ("Gracias", "Thank you"),
        ("Por favor", "Please"),
        ("Adiós", "Goodbye"),
        ("Buenos días", "Good morning"),
        ("¿Cómo estás?", "How are you?"),
     ]},
    {"title": "Chemistry: Periodic Table", "subject": "Chemistry", "cover_emoji": "⚗️", "author": "ElementEd",
     "cards": [
        ("H", "Hydrogen, atomic number 1."),
        ("He", "Helium, noble gas, atomic number 2."),
        ("C", "Carbon, atomic number 6, basis of organic chemistry."),
        ("O", "Oxygen, atomic number 8."),
        ("Na", "Sodium, alkali metal, atomic number 11."),
        ("Fe", "Iron, transition metal, atomic number 26."),
     ]},
    {"title": "Physics: Newton's Laws", "subject": "Physics", "cover_emoji": "🍎", "author": "PhysPro",
     "cards": [
        ("First Law", "Object in motion stays in motion unless acted upon by a force."),
        ("Second Law", "F = m * a (force equals mass times acceleration)."),
        ("Third Law", "For every action there is an equal and opposite reaction."),
        ("Inertia", "Tendency of an object to resist changes in motion."),
        ("Momentum", "Mass times velocity (p = mv)."),
     ]},
    {"title": "Psychology: Memory", "subject": "Psychology", "cover_emoji": "🧠", "author": "MindMap",
     "cards": [
        ("Short-term memory", "Holds a small amount of information for seconds."),
        ("Long-term memory", "Stores information for days to decades."),
        ("Encoding", "Process of converting info into a memory form."),
        ("Retrieval", "Accessing stored information from memory."),
        ("Schema", "Mental framework organizing information."),
     ]},
    {"title": "SAT Vocabulary", "subject": "Language", "cover_emoji": "📚", "author": "PrepPal",
     "cards": [
        ("Ephemeral", "Lasting for a very short time."),
        ("Ubiquitous", "Present or found everywhere."),
        ("Pragmatic", "Dealing with things sensibly and realistically."),
        ("Ambivalent", "Having mixed feelings or contradictory ideas."),
        ("Esoteric", "Intended for or understood by only a small group."),
     ]},
    {"title": "World Geography", "subject": "Geography", "cover_emoji": "🌍", "author": "GlobeTrot",
     "cards": [
        ("Nile", "Longest river in Africa."),
        ("Everest", "Highest mountain on Earth, in the Himalayas."),
        ("Amazon", "Largest rainforest, in South America."),
        ("Sahara", "Largest hot desert in the world."),
        ("Pacific", "Largest and deepest ocean."),
     ]},
]

async def cleanup_test_sets():
    """Remove user-created study sets that look like test clutter (titles with 'test' patterns)."""
    import re
    res = await db.study_sets.delete_many({
        "is_mine": True,
        "title": {"$regex": r"(?i)(test_custom|^test\b|testing_|test set|biology test)"},
    })
    if res.deleted_count:
        logger.info(f"Cleaned up {res.deleted_count} test study sets")

async def cleanup_duplicate_blocked_apps():
    """Remove duplicate blocked apps with the same name (keep the oldest), so the home
    screen doesn't show repeats accumulated from previous onboarding replays."""
    docs = await db.blocked_apps.find({}, {"_id": 0}).sort("name", 1).to_list(1000)
    seen: Dict[str, str] = {}
    to_delete: List[str] = []
    for d in docs:
        key = (d.get("name") or "").strip().lower()
        if not key:
            continue
        if key in seen:
            to_delete.append(d["id"])
        else:
            seen[key] = d["id"]
    if to_delete:
        await db.blocked_apps.delete_many({"id": {"$in": to_delete}})
        logger.info(f"Cleaned up {len(to_delete)} duplicate blocked apps")

async def seed_data():
    count = await db.study_sets.count_documents({})
    if count > 0:
        return
    logger.info("Seeding study sets...")
    for s in SEED_STUDY_SETS:
        cards = [Flashcard(term=t, definition=d) for t, d in s["cards"]]
        obj = StudySet(
            title=s["title"], subject=s["subject"], cover_emoji=s["cover_emoji"],
            author=s["author"], cards=cards, is_public=True, is_mine=False,
            description=f"A curated set on {s['subject']}",
        )
        await db.study_sets.insert_one(obj.model_dump())

    # Seed blocked apps
    if await db.blocked_apps.count_documents({}) == 0:
        for app_info in [
            {"name": "Instagram", "icon": "📸", "daily_limit_minutes": 30, "used_minutes": 28},
            {"name": "TikTok", "icon": "🎵", "daily_limit_minutes": 20, "used_minutes": 18},
            {"name": "Twitter / X", "icon": "🐦", "daily_limit_minutes": 25, "used_minutes": 10},
        ]:
            await db.blocked_apps.insert_one(BlockedApp(**app_info).model_dump())

    await get_or_create_profile()

@app.on_event("startup")
async def on_startup():
    await seed_data()
    await cleanup_test_sets()
    await cleanup_duplicate_blocked_apps()

app.include_router(api_router)
app.add_middleware(
    CORSMiddleware, allow_credentials=True,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
