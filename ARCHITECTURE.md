# ARCHITECTURE.md — Autonomous Cognitive Offloader

## 1. System Vision

An AI-driven, web-first life scheduler and conversational logger. It is not a passive dashboard or checkbox habit tracker — it is a **neuro-symbolic cognitive-load offloader** that:

- Refuses vague goals and interrogates them into concrete, schedulable parameters ("Socratic grilling").
- Digitizes Bullet Journal (BuJo) rapid-logging — tasks, events, notes, priorities, migrations — from natural conversation instead of hand-drawn symbols.
- Guarantees mathematically conflict-free scheduling via a deterministic constraint solver, never an LLM guessing at time slots.
- Remembers everything the user has ever told it (academic syllabi, personal constraints, health nuances) so nothing has to be re-explained.
- Reinforces habit completion and schedule adherence with gamified audio feedback.

The core architectural principle is a **strict separation of concerns between language and arithmetic**: LLMs are excellent at understanding messy human input but hallucinate at temporal/arithmetic reasoning; constraint solvers are the exact opposite. The system exploits both.

## 2. High-Level Architecture (Web / Cloud-Edge)

```
┌───────────────────────────────────────────┐
│ Frontend: Next.js (React, PWA-enabled) │
│ - Conversational chat UI (primary screen) │
│ - Life-stats dashboard ("The Ledger") │
│ - Web Audio feedback engine │
│ - Web Speech / voice input (optional) │
└─────────────────────┬───────────────────────┘
                       │ HTTPS / WebSockets
                       ▼
┌───────────────────────────────────────────┐
│ Stateless Backend: FastAPI (Python 3.11+) │
│ - Socratic interrogation orchestration │
│ - Pydantic schema validation │
│ - BuJo event ingestion & routing │
│ - Syllabus/PDF ingestion pipeline │
└──────────────┬──────────────────┬──────────┘
               │                  │
   Structured JSON /              │ Fixed + flexible task list
   Socratic prompts                │ (15-min interval ticks)
               ▼                  ▼
┌────────────────────────┐  ┌───────────────────────────┐
│ Google Gemini API       │  │ Google OR-Tools (CP-SAT)  │
│ - gemini-1.5-flash      │  │ - Native C++ solver       │
│ - Structured Outputs    │  │ - Solves in <50ms         │
│   (response_schema)     │  │ - Zero-overlap guarantee  │
│ - Extraction + probing  │  │ - Priority preemption     │
└────────────────────────┘  └─────────────┬─────────────┘
                                          │
                                          ▼
                         ┌───────────────────────────────┐
                         │ Supabase (PostgreSQL 16)      │
                         │ - pgvector: syllabi + nuance   │
                         │ - schedule_items: calendar     │
                         │ - telemetry_logs: BuJo stream  │
                         │ - tracker_definitions: EAV     │
                         └───────────────────────────────┘
```

## 3. Neuro-Symbolic Operational Boundary (Hard Rule)

| Component | Responsibility | Never Does |
|---|---|---|
| **LLM (Google Gemini API, `gemini-1.5-flash`)** | Interviewer, extractor, synthesizer. Parses conversation into schema-constrained JSON via Structured Outputs. Runs Socratic clarification loop. Converts solver output back into friendly language. | Never invents, places, or modifies a time slot. Never does calendar arithmetic. |
| **CP-SAT** | Deterministic scheduler. Places intervals, enforces zero overlap, resolves priority preemption. | Never interprets natural language. Never decides *what* a task means — only *when* it fits. |

This boundary is non-negotiable in implementation: any code path that lets the LLM directly write a `start_time`/`end_time` to `schedule_items` is a bug.

### 3.1 Structured Extraction via Gemini Native Structured Outputs

The NLP extraction layer uses the **Google Gemini API** (`gemini-1.5-flash`) with its native **Structured Outputs** feature (`response_schema`). Pydantic v2 models are passed directly to the API as the response schema; the SDK returns an already-parsed, validated instance. This replaces the previous prompt-based JSON approach on Groq, where the schema lived only in the prompt text and the model was merely *asked* to comply.

What this changes:

- **Schema-conformant output by construction.** The API constrains generation to the supplied schema, so responses are always well-formed JSON arrays/objects matching the model — no fenced code blocks, no conversational chatter wrapped around the payload, no missing required fields, no invented keys.
- **Closed vocabularies are enforced at the API layer.** Fields typed as `Literal[...]`/`Enum` (e.g., `category` in `TelemetryDataPoint`, and `unit` wherever a fixed unit vocabulary is defined) can only take listed values. This closes the unit-hallucination hole of the prompt-based implementation, where the model could emit free-form or inconsistent unit strings (`"hrs"`, `"hours"`, `"h"`).
- **Backend validation is still mandatory.** Structured Outputs guarantee *shape*, not *truth*. A schema-valid payload can still contain a wrong value (e.g., misheard "8" for "18"). Every response is re-validated by Pydantic in `gemini_client.py`, and the JIT registration rules in `UNIVERSAL_TRACKING_SPEC.md` (Section 4) remain the deterministic authority on what gets stored. On validation failure: retry once at `temperature=0`, then fall back to a clarifying question to the user — never a silent write.

**Backend module:** `app/services/gemini_client.py` (replaces `app/services/groq_client.py`). It is the only module allowed to call the Gemini API; every extraction, interrogation, and synthesis call goes through it.

```python
# app/services/gemini_client.py
import os
from typing import Type, TypeVar
from pydantic import BaseModel
from google import genai
from google.genai import types

T = TypeVar("T", bound=BaseModel)

_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")  # single source of truth for model id

async def extract(schema: Type[T], system_prompt: str, user_message: str) -> T:
    """Call Gemini with a Pydantic v2 model as response_schema; return a validated instance."""
    response = await _client.aio.models.generate_content(
        model=GEMINI_MODEL,
        contents=user_message,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=schema,   # Pydantic v2 model passed directly
            temperature=0,
        ),
    )
    return schema.model_validate(response.parsed)  # re-validate; never trust blindly
```

**Model configuration.** The model ID is read from the `GEMINI_MODEL` environment variable and referenced nowhere else in the codebase. Google retires Gemini model IDs on a short cadence, so swapping the model must be a one-line config change, not a code change.

**Schema compatibility note.** `TelemetryDataPoint.value` is typed `Any` in `UNIVERSAL_TRACKING_SPEC.md`. Gemini's `response_schema` works from a restricted OpenAPI-style subset and an unconstrained `Any` may be rejected or produce a loose schema. If that happens, replace it with an explicit union (`Union[float, bool, str]`) or with a `value_type` enum plus separate `value_number` / `value_bool` / `value_text` fields, and have the JIT layer collapse them back into a single `value`. Verify against the installed SDK version before building the full pipeline on top of it.

## 4. Core Mechanics

### 4.1 Socratic Goal Interrogation (Two-Pass Loop)

1. **Pass 1 — Ambiguity Evaluation**: incoming input is checked against a strict `GoalInterrogationSchema`. If mandatory fields (frequency, duration, deadline, current proficiency) are missing, the LLM responds *only* with up to 2 sharp clarifying questions. It does not schedule anything at this stage.
2. **Pass 2 — Parameter Emission**: once clarified, the LLM emits a validated JSON payload (task name, duration, deadline, priority 1–10, preemptible flag) which is handed to the solver — never rendered as prose scheduling logic.

Example emitted payload:
```json
{
  "event_type": "exam_prep",
  "subject": "Computer Networks",
  "deadline": "2026-09-29T09:00:00",
  "total_hours_required": 14,
  "min_chunk_minutes": 60,
  "max_chunk_minutes": 120,
  "priority_level": 10,
  "preemptible": false
}
```

### 4.2 Deterministic Scheduling (Google OR-Tools CP-SAT)

- **Discretization**: each rolling 7-day window is divided into 15-minute ticks (`7 days × 24h × 4 ticks/hr = 672 ticks`).
- **Fixed intervals** (classes, labs, confirmed exams, sleep window): hard constraints, `is_fixed = True`, never moved.
- **Flexible intervals** (study blocks, habits, discretionary tasks): modeled as `OptionalIntervalVar` with a presence boolean, allowing the solver to drop/bump low-priority items entirely if the schedule is full.
- **Non-overlap**: `model.AddNoOverlap([all_intervals])` — a hard constraint, guaranteeing zero double-booking.
- **Objective**: maximize `Σ(presence_i × priority_weight_i)`, where exam-tier tasks carry weight ~1000, habit-tier ~100–200, discretionary ~10–20. When capacity is insufficient, the solver automatically sets `presence = 0` for the lowest-weight tasks first.
- **Solve time**: sub-50ms on standard server CPU.

```python
from ortools.sat.python import cp_model

def solve_weekly_schedule(fixed_events, flexible_tasks, total_horizon_ticks=672):
    """
    1 tick = 15 minutes. fixed_events: [{'id','start','end'}]
    flexible_tasks: [{'id','duration','priority','deadline'}]
    """
    model = cp_model.CpModel()
    task_intervals = []
    task_presences = {}
    task_starts = {}
    objective_terms = []

    for idx, event in enumerate(fixed_events):
        duration = event['end'] - event['start']
        fixed_int = model.NewIntervalVar(event['start'], duration, event['end'], f"fixed_{idx}")
        task_intervals.append(fixed_int)

    for task in flexible_tasks:
        tid = task['id']
        dur = task['duration']
        deadline = task.get('deadline', total_horizon_ticks)
        presence = model.NewBoolVar(f"presence_{tid}")
        start_var = model.NewIntVar(0, max(0, deadline - dur), f"start_{tid}")
        end_var = model.NewIntVar(0, deadline, f"end_{tid}")
        opt_interval = model.NewOptionalIntervalVar(start_var, dur, end_var, presence, f"interval_{tid}")
        task_intervals.append(opt_interval)
        task_presences[tid] = presence
        task_starts[tid] = start_var
        objective_terms.append(presence * (task['priority'] * 1000))

    model.AddNoOverlap(task_intervals)
    model.Maximize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 0.05
    status = solver.Solve(model)

    scheduled, bumped = [], []
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for task in flexible_tasks:
            tid = task['id']
            if solver.BooleanValue(task_presences[tid]):
                scheduled.append({"id": tid, "start_tick": solver.Value(task_starts[tid]), "duration_ticks": task['duration']})
            else:
                bumped.append(tid)
    return {"scheduled": scheduled, "bumped": bumped}
```

### 4.3 Conversational BuJo Telemetry (Zero-Form Logging)

The Rapid Logging grammar of Bullet Journaling maps directly onto software types:

| BuJo symbol | Meaning | System type | Storage |
|---|---|---|---|
| `·` / `[ ]` | Task | `ScheduledTask` | `schedule_items` (CP-SAT managed) |
| `X` | Completed | Resolution flag | `is_completed = true` |
| `O` | Event (fixed) | `FixedInterval` | `schedule_items` (`is_fixed = true`) |
| `-` | Note / venting | Unstructured note | `semantic_contexts` (vector) |
| `!` / `*` | Priority | Weight multiplier | CP-SAT objective weight |
| `>` | Migration | Auto-preemption | Solver-driven rescheduling, `migration_count` |
| `△` | Memory / milestone | Episodic log | `semantic_contexts` |
| Grid dots | Habit / metric | Dynamic telemetry | `telemetry_logs` (EAV/JSONB) |

Given: *"i just did 3 sets of 10 reps for pushups... lifted 20 kilos for the deadlifts... having a protein bar and a banana"* — this is routed through a Pydantic-constrained extraction call, never free text generation:

```python
from pydantic import BaseModel
from typing import List, Optional

class ExerciseDetail(BaseModel):
    name: str
    sets: Optional[int] = None
    reps: Optional[int] = None
    weight_kg: Optional[float] = None

class NutritionDetail(BaseModel):
    item: str
    quantity: Optional[str] = None

class ActivityExtraction(BaseModel):
    has_activity: bool
    exercises: List[ExerciseDetail] = []
    nutrition: List[NutritionDetail] = []
    habits_completed: List[str] = []
    discovered_user_nuances: List[str] = []
```

The result silently updates `activity_logs`/`telemetry_logs`, increments streak counters, and triggers the success jingle — no checkbox, no form.

**Socratic Migration Check**: if a discretionary task rolls forward (`migration_count`) more than 3 times without completion, the agent halts auto-rescheduling and asks the user to explicitly commit to it or strike it through — replicating Ryder Carroll's manual "weeding" step.

### 4.4 Dual-Memory Context Engine ("Never Re-Explain")

Two distinct vector collections inside the same Postgres/pgvector instance — kept semantically separate to avoid polluting one domain with the other:

**A. Academic Syllabus / Coursework RAG**
- Ingestion via PyMuPDF/pdfplumber; syllabi chunked (300–500 tokens, 50-token overlap) and embedded.
- Metadata schema includes `subject`, `module_number`, `topics`, `textbook_ref`, `lab_experiments`.
- On "I'm terrified of Subnetting," semantic retrieval pulls exact module definitions, textbook references, and lab requirements to compute required prep hours.

**B. Episodic Life Context**
- Every conversational turn is scanned for implicit/explicit personal constraints ("Screen work past 10 PM gives me migraines," "85% attendance required Tuesday 8 AM").
- Stored in the same vector table tagged `context_type = 'episodic_constraint'`.
- On a later conflicting request ("plan an all-nighter Tuesday"), semantic retrieval surfaces the stored constraint and the LLM pushes back citing it before passing bounds to CP-SAT.

**What must never go in the vector store**: active calendar slots, streaks, exact numeric aggregates. Anything requiring exact SQL arithmetic (`SUM()`, `COUNT()`, date ranges, overlap checks) lives in relational tables — cosine similarity cannot determine if 2:00 PM overlaps 2:30 PM.

**Master State Card**: a small `user_profiles` row (sleep window, buffer minutes, max study hours/day, current semester) is injected into every system prompt as cheap, always-available context, separate from the heavier vector retrieval.

### 4.5 Dynamic Tracker Registry (Open-Ended Tracking)

New tracker categories ("track my spending," "track how often I visit friends") never require a schema migration. An Entity-Attribute-Value model handles this:

- `tracker_definitions`: one row per user-defined tracker (name, category, data_type, min/max, unit) — created on the fly after a short Socratic setup exchange ("Cups of coffee or exact mg? Total hours or bedtime consistency too?").
- `telemetry_logs`: append-only event stream, `entry_type` + `metadata JSONB`, referencing the tracker by name.

### 4.6 Automated Correlation Analytics

Rolling SQL aggregations (not ML, not vector search) surface lifestyle correlations directly from `telemetry_logs`:

```sql
SELECT
  logged_date,
  MAX(CASE WHEN metadata->>'name' = 'sleep' THEN (metadata->>'value')::float END) as sleep_hrs,
  MAX(CASE WHEN metadata->>'name' = 'stress' THEN (metadata->>'value')::float END) as stress_level
FROM telemetry_logs
GROUP BY logged_date
ORDER BY logged_date DESC LIMIT 30;
```

When a correlation crosses a threshold (e.g., sub-6h sleep → stress spike), the system doesn't just chart it — it feeds a hardened bound into the next CP-SAT solve (e.g., cap tomorrow's deep-work blocks to 45 minutes, enforce a 10:30 PM cutoff).

### 4.7 Gamified Audio Feedback

Pure Web Audio API, zero external asset files, zero cloud TTS dependency for sound effects:

```typescript
class SoundManager {
  private ctx: AudioContext | null = null;
  private initContext() {
    if (!this.ctx) this.ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
    if (this.ctx.state === 'suspended') this.ctx.resume();
  }

  // Dopamine Jingle: ascending C major arpeggio C5 -> E5 -> G5 -> C6
  playSuccessJingle() {
    this.initContext();
    const notes = [523.25, 659.25, 783.99, 1046.50];
    notes.forEach((freq, idx) => {
      const osc = this.ctx!.createOscillator();
      const gain = this.ctx!.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(freq, this.ctx!.currentTime + idx * 0.08);
      gain.gain.setValueAtTime(0.2, this.ctx!.currentTime + idx * 0.08);
      gain.gain.exponentialRampToValueAtTime(0.001, this.ctx!.currentTime + idx * 0.08 + 0.25);
      osc.connect(gain); gain.connect(this.ctx!.destination);
      osc.start(this.ctx!.currentTime + idx * 0.08);
      osc.stop(this.ctx!.currentTime + idx * 0.08 + 0.3);
    });
  }

  // Sad Trombone: descending brass slide Eb4 -> D4 -> Db4 -> C4 with pitch bend
  playSadTrombone() {
    this.initContext();
    const notes = [311.13, 293.66, 277.18, 261.63];
    notes.forEach((freq, idx) => {
      const osc = this.ctx!.createOscillator();
      const gain = this.ctx!.createGain();
      const startTime = this.ctx!.currentTime + idx * 0.32;
      const duration = idx === 3 ? 0.8 : 0.3;
      osc.type = 'sawtooth';
      osc.frequency.setValueAtTime(freq, startTime);
      if (idx === 3) osc.frequency.exponentialRampToValueAtTime(215.0, startTime + duration);
      gain.gain.setValueAtTime(0.18, startTime);
      gain.gain.exponentialRampToValueAtTime(0.001, startTime + duration);
      osc.connect(gain); gain.connect(this.ctx!.destination);
      osc.start(startTime); osc.stop(startTime + duration);
    });
  }
}
export const soundManager = new SoundManager();
```

Triggers: `playSuccessJingle()` on `activity_logged: true` or task completion; `playSadTrombone()` on `schedule_status: "LAGGING"`, a missed deadline, or a forced low-priority bump.

## 5. Database Schema (PostgreSQL + pgvector, Supabase/Neon)

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Master profile & global scheduling parameters
CREATE TABLE user_profiles (
  user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email VARCHAR(255) UNIQUE NOT NULL,
  sleep_start TIME NOT NULL DEFAULT '23:00:00',
  sleep_end TIME NOT NULL DEFAULT '07:00:00',
  buffer_minutes INT NOT NULL DEFAULT 15,
  max_study_hours_per_day INT NOT NULL DEFAULT 8,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Syllabus RAG + episodic life-nuance vectors, kept in one table but tagged
CREATE TABLE semantic_contexts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES user_profiles(user_id) ON DELETE CASCADE,
  context_type VARCHAR(30) NOT NULL, -- 'syllabus_module' | 'episodic_constraint'
  subject VARCHAR(100),
  raw_content TEXT NOT NULL,
  embedding VECTOR(1536),
  metadata JSONB DEFAULT '{}'::jsonb,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX idx_semantic_embedding ON semantic_contexts USING ivfflat (embedding vector_cosine_ops);

-- Unified calendar, exclusively CP-SAT managed
CREATE TABLE schedule_items (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES user_profiles(user_id) ON DELETE CASCADE,
  title VARCHAR(255) NOT NULL,
  category VARCHAR(50) NOT NULL, -- 'exam' | 'class' | 'lab' | 'habit' | 'study_session'
  start_time TIMESTAMP WITH TIME ZONE,
  end_time TIMESTAMP WITH TIME ZONE,
  duration_minutes INT NOT NULL,
  priority INT NOT NULL DEFAULT 5, -- 10 = immovable exam, 1 = discretionary
  is_fixed BOOLEAN DEFAULT FALSE,
  deadline TIMESTAMP WITH TIME ZONE,
  is_completed BOOLEAN DEFAULT FALSE,
  migration_count INT DEFAULT 0,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX idx_schedule_user_dates ON schedule_items (user_id, start_time, end_time);

-- Dynamic tracker registry (EAV)
CREATE TABLE tracker_definitions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES user_profiles(user_id) ON DELETE CASCADE,
  name VARCHAR(100) NOT NULL,
  category VARCHAR(50) NOT NULL, -- 'metric_scale' | 'binary_habit' | 'volume' | 'state'
  data_type VARCHAR(20) NOT NULL, -- 'integer' | 'boolean' | 'float' | 'string'
  val_min FLOAT,
  val_max FLOAT,
  unit VARCHAR(30),
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Raw telemetry event stream (the digital rapid log)
CREATE TABLE telemetry_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES user_profiles(user_id) ON DELETE CASCADE,
  entry_type VARCHAR(20) NOT NULL, -- 'task' | 'event' | 'note' | 'metric' | 'memory'
  content TEXT,
  metadata JSONB DEFAULT '{}'::jsonb,
  logged_date DATE NOT NULL DEFAULT CURRENT_DATE,
  logged_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX idx_telemetry_user_date ON telemetry_logs (user_id, logged_date);
CREATE INDEX idx_telemetry_gin ON telemetry_logs USING gin (metadata);
```

## 6. Pydantic Schemas (Extraction Layer)

All models in this section are passed directly to `gemini_client.extract(...)` as the Gemini `response_schema` (see Section 3.1). Conversational telemetry extraction is governed by `UNIVERSAL_TRACKING_SPEC.md`, which supersedes the domain-specific telemetry schemas below.

```python
from pydantic import BaseModel, Field
from typing import List, Optional

class GoalInterrogationSchema(BaseModel):
    is_ambiguous: bool = Field(..., description="True if goal lacks frequency, duration, or measurable bounds.")
    clarifying_questions: Optional[List[str]] = Field(None, description="Max 2 targeted Socratic questions if ambiguous.")
    task_name: Optional[str] = None
    target_frequency_per_week: Optional[int] = None
    session_duration_minutes: Optional[int] = None
    deadline_iso: Optional[str] = None
    priority_level: Optional[int] = Field(default=5, ge=1, le=10)
    detected_constraints: Optional[List[str]] = Field(default=[])

class TelemetryItem(BaseModel):
    tracker_name: str
    entry_type: str  # 'metric' | 'binary_habit' | 'note' | 'event'
    value: float | str | bool
    unit: Optional[str] = None

class ConversationalUpdate(BaseModel):
    has_telemetry: bool
    telemetry_items: List[TelemetryItem] = []
    habits_completed: List[str] = []
    discovered_constraints: List[str] = []
```

## 7. Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Frontend | Next.js (React, App Router, PWA) | Single codebase across iOS/Android/desktop browsers, add-to-home-screen |
| Backend API | FastAPI (Python 3.11+) | Async, native OR-Tools bindings, strict Pydantic validation |
| LLM Inference | Google Gemini API (`gemini-1.5-flash`, via `google-genai` SDK) | Fast Flash-tier inference, free tier, native Structured Outputs (`response_schema`) with Pydantic v2 |
| Speech-to-Text | Google Gemini API (native audio input) | One provider for STT + extraction, no separate STT vendor |
| Text-to-Speech | Browser-native Web Speech API (`window.speechSynthesis`) | $0 cost, zero latency, no external dependency |
| Constraint Solver | Google OR-Tools CP-SAT | Deterministic, <50ms solves, zero hallucination |
| Database | PostgreSQL + pgvector (Supabase / Neon) | Relational + vector in one instance, free tier |
| Document Ingestion | PyMuPDF / pdfplumber | Local, robust syllabus/timetable PDF parsing |
| Audio Feedback | Web Audio API (oscillator synthesis) | Zero asset files, zero cost |

## 8. Deployment (Zero-Cost Path)

1. **Database**: free Supabase project; run the schema above via SQL editor.
2. **LLM/STT**: free Google Gemini API key from Google AI Studio. Set `GEMINI_API_KEY` and `GEMINI_MODEL` (default `gemini-1.5-flash`) as backend environment variables. Free-tier rate limits are project-specific; check the AI Studio console.
3. **Backend**: deploy FastAPI to Render or Koyeb free tier (`uvicorn main:app --host 0.0.0.0 --port $PORT`).
4. **Frontend**: deploy Next.js to Vercel free tier, pointing `NEXT_PUBLIC_API_URL` at the backend.

Total steady-state cost at hackathon/demo scale: **$0/month**.

## 9. Non-Functional Requirements

- End-to-end voice-to-answer latency: < 1.5s (target < 1s: STT + Gemini Flash-tier extraction + <50ms solve; TTS is client-side/instant). Measure p50/p95 for the Gemini calls during integration and record them here.
- Extraction reliability: 100% of LLM extraction responses must parse into the target Pydantic model; failures trigger one retry, then a clarifying question.
- CP-SAT solve time: < 50ms per re-plan, even under full-week horizon (672 ticks).
- Zero calendar collision rate: enforced structurally via `AddNoOverlap`, not tested for — guaranteed by construction.
- Data isolation: row-level scoping by `user_id` on every table.
- Responsive: full parity between mobile PWA and desktop browser.

## 10. Explicit Non-Goals / Guardrails

- The LLM must never emit a `start_time`/`end_time` directly into `schedule_items`.
- Vector search is never used for anything requiring exact arithmetic (overlaps, streak counts, sums) — those are SQL-only.
- No hardcoded tracker columns — all new tracked variables go through `tracker_definitions`, never a migration.
- No pure local desktop deployment for the primary product — the shipped surface is a web PWA reachable from any device via one URL.