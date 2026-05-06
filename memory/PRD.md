# FocusLearn — PRD

## Vision
Transform doomscrolling into measurable study time. Users block distracting apps with a daily limit; when they hit it, the only way back in is to answer a quiz correctly. Correct answers earn points that are redeemable for extra screen time.

## User Choices
- Auth: none (single local profile for MVP)
- Quiz generation: GPT-5.2 via Emergent LLM key + deterministic flashcard fallback
- Screen-time blocking: SIMULATED in-app lock screen (no OS-level blocking)
- Marketplace: seeded with 10 public study sets across 9 subjects
- Economy: 5 pts per correct answer + 25 pts completion bonus; 150 pts = 15 extra minutes

## Feature Summary
- Onboarding (blue owl mascot, stacked cards, serif title)
- Tabs: Home, Marketplace, Practice, Profile (pill floating bar)
- Home: points balance, daily/weekly/monthly screen-time chart, blocked-apps list with usage, "Add limit" modal, "Simulate app block" CTA
- Marketplace: search, subject filters, tilted study-set cards, FAB to create own
- Study set detail: flashcard flipper with stacked back cards, Start Practice Quiz
- Quiz: progress bar, 4-option MCQ, feedback w/ explanation, result screen with points
- Lock screen: full-screen dark overlay; choose a study set; must pass 80% to unlock
- Profile/Rewards: redeem 150/300/600 pts for 15/30/60 extra minutes

## Smart business enhancement
Points economy + AI-generated quizzes per study set → builds habit loop (block → study → earn → redeem), increasing daily active time and viral sharing of user-created sets.

## Next Action Items
- Add streak tracking + push-style daily reminder
- Allow sharing a study set via public link
- Add social leaderboard for community engagement

## Iteration 2 additions (Feb 2026)
- Redesigned flashcards (segmented progress, black card, tap-to-reveal, got-it/more-help/not-quite self-assessment, continue-to-next-card button)
- Redesigned quiz UI (blue subject pill, 2x2 option grid, confetti on correct, red on wrong)
- Global font switched to SF Pro (system font)
- Marketplace Preview modal (bookmark toggle, sample flashcards, Save to Wallet + Study now)
- Wallet: saved study sets shown in Profile tab + `Saved` filter chip in Marketplace
- True Light + Dark themes (semantic palettes, not inversion). Dark: #0E1324 bg, #6B8CFF primary. Toggle in Profile → Appearance, persisted via AsyncStorage.

## Iteration 3 additions (Feb 2026)
- Real 3D flip animation on flashcards (Animated.spring rotateY) with slide-in for next card.
- Quiz polish: spring slide/fade between questions, press-bounce on option select, pulse on correct answer reveal, radial confetti with rotation.
- Marketplace redesigned with oversized-bento aesthetic: large rounded search bento (search + filter + multi-color chip row), Featured sets horizontal carousel of big colored tiles per subject, All sets list with per-subject colored avatars + progress bars.
- New SUBJECT_ACCENTS palette (orange/mint/coral/lavender/butter/indigo/aqua/rose/teal) works in both themes.
- Home hero redesigned as oversized bento (soft outer bento + dark inner stat card) inspired by the crypto-dashboard reference.
- Backend: added is_saved field to StudySet Pydantic model so it is returned in all list/get responses.

## Iteration 4 additions (Feb 2026)
- Practice tab redesigned: primary mode is FLIPPABLE FLASHCARDS. Each card flip reveals 3 self-assess outcomes — got_it / more_practice / not_quite — that drive an in-session shuffle queue until all cards are mastered.
- Secondary mode: MCQ practice quiz worth 1.5x a lock-challenge quiz (7 pts/correct + 38 pass bonus vs 5 + 25).
- New endpoints: POST /api/flashcard-sessions/start, /review, /complete, GET /api/flashcard-sessions/{id}.
- Profile model: adds username (editable), referral_code (auto-generated), level, xp_to_next. PATCH /api/profile updates username.
- New /api/social/leaderboard endpoint returns seeded friends + 'me'.
- Navigation: 5 tabs with labels under icons — Home · Discover · Practice · Wallet · Social.
- Home greeting: "Hi, {username}, Let's learn something new".
- Wallet tab (was Profile): Lv badge + XP bar bento, stat mini-cards (balance / redeemed), redeem tiers, saved sets, Appearance toggle.
- Social tab (new): referral code bento + share button, streak / level / friends mini-cards, leaderboard list with 'you' highlight.

## Iteration 5 polish (Feb 2026)
- Quiz option cards taller (min 210px) and solid dark in idle state; selected highlights blue; correct/wrong reveal still green/red with confetti.
- Wallet redeem section rebuilt as a horizontal swipe row of 4 LARGE rounded square tiles (266x220) — 15/30/60/120 minutes — each with locked/unlocked badge, emoji, title, and in-card Redeem button.
- Home greeting title weight softened to 600 with maxWidth 260 so it wraps naturally.
- Home Balance card now wraps a blue LinearGradient (#6EA8FF → #2F5BFF → #1E3A8A) + dotted grain overlay with a translucent dark inner panel.
- Discover runtime error fixed (profile state declaration was missing).
- New /settings route hosts the Light/Dark theme selector; Wallet has an 'App settings' row linking to it (theme toggle removed from Wallet).
