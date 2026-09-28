# PRD.md — Autonomous Cognitive Offloader

## 1. Executive Summary

Traditional scheduling and habit-tracking tools force users to manually input vague tasks and manage complex UI forms, creating high friction and executive fatigue. When unexpected deadlines arise, static calendars fail to dynamically re-prioritize or resolve conflicts. Meanwhile, conversational loggers record only superficial binary streaks, and quantified-self apps are passive and rigid.

This product is an **Autonomous Cognitive Offloader**: an AI-driven life scheduler and conversational logger that turns casual conversation into structured goals, and uses a mathematical constraint solver (Google OR-Tools CP-SAT) to dynamically and provably optimize the user's timeline — never a generic advice tool, and never "just another dashboard."

**Who is affected**: students, young professionals, and power users struggling with executive dysfunction, goal clarity, and time management while juggling academics, health, and habits.

**Why it matters**: leaving this unsolved leads to chronic burnout, abandoned goals, poor exam readiness, and mental fatigue from over-managing passive productivity apps instead of making tangible progress.

## 2. Problem Statement

- **Zero interrogation**: existing auto-schedulers (Motion, Reclaim.ai, SkedPal) accept "study" as a task with a deadline — they don't break down a syllabus, probe for weak topics, or deduce prep time.
- **Superficial logging**: conversational loggers (Folk, Habit Mind) record binary streaks, not multi-variable logs (sets × reps × weight + nutrition + mood).
- **Passive aggregation**: quantified-self tools (Exist.io, Gyroscope) pull health data into charts but never intervene in the calendar.
- **Siloed academics**: AI study planners treat coursework in isolation from gym schedule, health data, or daily life.
- **High manual overhead**: even the best manual system — Bullet Journaling — requires 30–60 minutes of setup per month and constant handwritten migration of unfinished tasks, causing high dropout.

No single product combines Socratic goal clarification, deterministic conflict-free scheduling, syllabus-aware exam prep, and zero-friction conversational telemetry into one system.

## 3. Product Vision & Unique Value Proposition

**What is different**: a neuro-symbolic architecture pairing an aggressive, Socratic conversational agent with a deterministic C++ constraint engine (Google OR-Tools CP-SAT). The agent refuses vague intent and grills the user into concrete constraints; the solver mathematically guarantees zero calendar overlaps and auto-preempts low-priority items when urgent deadlines arrive.

**Current alternative**: users juggle fragmented tools — passive auto-schedulers, manual habit trackers, static calendars — each requiring constant manual maintenance and none of which proactively interrogates vague goals or protects exam prep time.

**Why it matters**: this eliminates administrative mental load entirely. The user never has to decide what to do next, re-explain context, or manually reschedule — the system does it, and defends it, automatically.

## 4. Target Personas

### Persona A — The Overwhelmed Engineering Student
- Juggling a fixed class timetable, lab exams, internals, and personal habits (gym, sleep, social life).
- States vague intentions ("I should study more") without concrete plans.
- Panics when a surprise exam is announced and existing habits/plans get crushed under stress.
- Wants: someone (or something) to tell them exactly what to do next, with zero setup overhead.

### Persona B — The Quantified-Self Habit Tracker
- Already logs workouts, meals, mood, sleep, and wants to track novel things on a whim (spending, screen time, social visits).
- Frustrated by dropdown-menu logging (MyFitnessPal) and wants to just talk/type naturally.
- Wants to see correlations between lifestyle variables (e.g., sleep vs. stress) without manually cross-referencing paper trackers.

## 5. Core Features

1. **Grill Mode (Socratic Goal Interrogation)** — the AI refuses vague goals ("I want to exercise more") and asks targeted, specific questions (frequency, duration, time window) until the goal is schedulable. Enforced by a strict Pydantic schema passed to the Google Gemini API as its native structured-output `response_schema` — every interrogation turn is returned as a schema-valid object, and the system will not proceed to scheduling while `is_ambiguous = true`.
2. **Deterministic CP-SAT Scheduling** — a math engine mathematically guarantees zero calendar overlaps and automatically bumps/shortens low-priority tasks when high-priority items (exams) arrive. This runs in milliseconds and never hallucinates a conflicting time.
3. **Academic Syllabus Ingestion (RAG)** — upload a timetable, syllabus, or assignment PDF (or none at all — context accumulates as it's mentioned in conversation). When a user expresses weakness in a topic, the system retrieves exact module content and deduces required prep hours before a deadline.
4. **Episodic Life Context ("Never Re-Explain")** — personal constraints and health nuances mentioned once (e.g., "screen work past 10 PM gives me migraines," "85% attendance required for Tuesday 8 AM class") are remembered and enforced as guardrails in all future scheduling, without the user repeating themselves.
5. **Conversational BuJo Telemetry (Universal Telemetry Engine)** — the user describes their day in one casual sentence ("did 3x10 pushups, 20kg deadlifts, ate a protein bar") and the system silently extracts and files structured records across multiple trackers — no checkboxes, no forms. Extraction uses Gemini's structured-output mode with a single generic `(entity, value, unit, category)` schema, so the response is always a schema-valid array of data points regardless of domain.
6. **Dynamic, Open-Ended Tracking (Zero-Schema)** — the user can ask to track literally anything (spending, outfits, friend visits). The Universal Telemetry Engine auto-registers any new entity just-in-time from Gemini's structured output and routes future mentions to it — no schema migration and no code change required. See `UNIVERSAL_TRACKING_SPEC.md`.
7. **Automated Lifestyle Correlation** — the system surfaces relationships between logged variables (e.g., "sub-6-hour sleep nights correlate with a stress spike the next day") and proactively hardens scheduling constraints in response, rather than just displaying a chart.
8. **Gamified Audio Feedback** — a dopamine-rewarding ascending jingle plays on habit/task completion; a descending "sad trombone" plays when the user falls behind schedule or a task fails — instant, checkbox-free reinforcement.
9. **Socratic Migration Check** — if a task has been auto-rescheduled forward more than 3 times without completion, the system stops silently re-scheduling it and asks the user to either commit to a real slot or strike it from the list, preventing an ever-growing backlog of ignored tasks.

## 6. User Scenarios

### Scenario A — Vague Goal Interrogation
> **User**: "I want to start studying Networks more."
> **AI**: "Networks covers 5 units. How many have you already read, and what's the exam format? I need real numbers before I can plan this."
> **User**: "3 chapters. I know chapter 1 well, haven't touched 2 or 3."
> **AI**: "That's 6 hours of focused prep needed. Your week is tight — I'm cutting your gym sessions from 90 to 45 minutes this week to fit it in. Confirm?"

### Scenario B — Sudden High-Priority Exam Arrival
> **User**: "I have my Networks internal next Tuesday!"
> The system marks it priority 10 (immovable), computes required hours from the ingested syllabus, and re-solves the week — automatically bumping or shortening lower-priority discretionary tasks (gaming, optional social plans) while defending sleep and class blocks. A sad trombone plays for any bumped task; the updated schedule renders instantly.

### Scenario C — Casual Multi-Variable Habit Logging
> **User**: "Just did 3 sets of 10 pushups and deadlifted 20kg. Having a protein bar now, feeling a bit hungry."
> The system extracts exercises (with sets/reps/weight), nutrition items, and marks the "workout" habit complete — updating the streak counter and playing the dopamine jingle — with zero forms or button taps.

### Scenario D — Socratic Task Migration Check
> A "clean out email inbox" task has silently rolled forward 4 times.
> **AI**: "You've rolled 'clean out email inbox' forward 4 times over the last 6 days. Is this actually important, or should we strike it and clear the load?"

## 7. Functional Requirements

- **Conversational Engine**: chat-first interface (primary screen); optional voice input via browser mic + Google Gemini API audio transcription, with spoken responses via the browser's native Web Speech API (`speechSynthesis`), kept under ~15–20 words for latency and listenability.
- **Document Ingestion**: PDF upload for timetables, syllabi, assignments, lab schedules; content is chunked, embedded, and made retrievable; missing documents are not required — content can accumulate purely from conversation.
- **Solver Integration**: every schedule mutation (new task, new exam, habit registration) triggers a CP-SAT re-solve over a rolling 7–14 day horizon; results are pushed back to the client in near real time.
- **Telemetry & Dynamic Trackers**: any user-defined tracker can be created via a short Socratic setup dialogue and logged against thereafter via free-text mentions.
- **Audio Feedback**: success/failure sound cues fire on well-defined backend signals (`activity_logged: true`, `schedule_status: "LAGGING"`), not client-side heuristics.
- **History / Ledger**: a secondary page (not the main screen) presents a running log of everything accomplished — tasks, habits, notes — over days, weeks, and months, plus visual stats (see UI/UX doc).

## 8. Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js (React, App Router, PWA), Tailwind, Recharts |
| Backend API | FastAPI (Python 3.11+), Pydantic v2 |
| **LLM provider (primary)** | **Google Gemini API (`gemini-1.5-flash`)** — Socratic interrogation ("Grill Mode"), Universal Telemetry extraction, response synthesis, and speech transcription, all via native Structured Outputs (`response_schema`) |
| Constraint solver | Google OR-Tools CP-SAT |
| Database | PostgreSQL + pgvector (Supabase / Neon) |
| Document ingestion | PyMuPDF / pdfplumber |
| Text-to-speech | Browser-native Web Speech API |
| Audio feedback | Web Audio API |

The LLM model ID is a single environment-level setting (`GEMINI_MODEL`), so it can be changed without code changes. Full detail lives in `ARCHITECTURE.md`.

## 9. Performance & Reliability Requirements

- Voice round-trip latency target: < 1.5s end-to-end. The low-latency target for Socratic interrogation and telemetry extraction is unchanged by the move to Gemini; each LLM call must fit inside this budget.
- **Model selection rationale**: `gemini-1.5-flash` was selected to balance high-speed conversational inference with strict JSON schema adherence — the Flash tier keeps interrogation and extraction turns fast, while native structured outputs (`response_schema`) keep every response schema-valid.
- **Extraction reliability**: 100% of LLM extraction responses must parse into the target Pydantic model. Structured outputs guarantee response *shape*; backend validation still checks values, and any failure triggers one retry and then a clarifying question rather than a silent write.
- CP-SAT solve time: < 50ms per re-plan.
- Zero calendar collision rate — structurally guaranteed, not just tested.
- Full responsiveness across mobile PWA and desktop browser from a single URL (no per-device install, no data left behind on device switch).
- Row-level data isolation per user in the cloud database.
- Runs at $0/month infrastructure cost on free tiers (Supabase, Google Gemini API, Vercel, Render) at hackathon/demo scale.

## 10. Success Metrics

- **Task adherence rate**: percentage of planned study blocks and habits successfully logged/completed.
- **Zero calendar collision rate**: verified structurally by the solver, monitored as a sanity metric.
- **Daily active conversational retention**: how often users return to log or check in via chat rather than abandoning the app (the core failure mode of both BuJo and existing habit trackers).

## 11. Demo-Day Deliverable

A live end-to-end demonstration showing:
1. The AI verbally/textually interrogating a vague goal into precise, scheduled constraints.
2. Ingestion of an urgent exam deadline, triggering CP-SAT to instantly reorganize the schedule, bump a lower-priority task, and play the warning sound.
3. A casual multi-exercise workout log via voice or text, with real-time metric extraction, instant streak update, and the completion jingle.

**Expected impact**: complete offloading of executive functioning, elimination of scheduling overhead, and higher exam-prep completion without burnout.

## 12. Out of Scope (v1)

- Native mobile apps (PWA covers mobile access for v1).
- True peer-to-peer local-first sync (CRDT-based) — deferred; the cloud-hybrid architecture already gives seamless cross-device access.
- LightGBM-based habit/energy prediction — deferred until sufficient local interaction telemetry exists; v1 uses an explicit utility/priority function.