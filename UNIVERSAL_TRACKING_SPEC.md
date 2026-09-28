# UNIVERSAL_TRACKING_SPEC.md — Zero-Schema Universal Telemetry Engine

## 0. Purpose & Scope

This document supersedes the domain-specific extraction schemas (`ExerciseLog`, `NutritionLog`, `ExerciseDetail`, `NutritionDetail`, `ActivityExtraction`) previously defined in `ARCHITECTURE.md`. Those schemas hardcoded the categories of life the system could understand — fitness and nutrition — which is precisely the "just another tracking app" failure mode the product exists to avoid.

This spec replaces them with a single, domain-agnostic extraction and storage pipeline: the **Zero-Schema Universal Telemetry Engine**. Every other part of `ARCHITECTURE.md` (the CP-SAT solver, the syllabus RAG, the episodic memory store, the audio feedback engine) is unaffected and remains as specified. Only the NLP extraction layer and its database routing are refactored.

---

## 1. Architectural Philosophy: The Omnivorous Ingestion Model

### 1.1 The problem with domain schemas

A schema like `ExerciseDetail { name, sets, reps, weight_kg }` is a bet that the developer correctly anticipated every dimension of life worth tracking. It didn't anticipate "pages read," "times I called my mom," "how anxious I felt," or the thousands of idiosyncratic things any individual user might decide to track on a random Tuesday. Every new domain (finance, mood, social contact, screen time, chores) would otherwise require a new Pydantic class, a new database column set, and a new code deploy — the exact "codebase update for a new habit" the product must never require.

### 1.2 The shift: rigid schema → universal EAV

The system moves from **N domain-specific schemas** to **one generic Entity-Attribute-Value (EAV) schema** that can represent any trackable fact about a user's life as a uniform tuple:

```
(entity, value, unit, category)
```

- `entity` is the *name* of the thing being tracked — open vocabulary, chosen by the LLM based on what the user actually said, not from a fixed enum.
- `value` is the *state or magnitude* — a float, int, bool, or free string, typed loosely (`Any`) at the schema layer and typed strictly (`float | int | bool | text`) only once it lands in storage.
- `unit` is optional context for numeric entities.
- `category` classifies *how the value should be interpreted and displayed* (a continuous metric, a binary habit, a countable volume, or a journal note) — this is the only closed vocabulary in the entire system, and it exists purely to drive chart rendering (per `UI_UX.md`) and correlation logic, never to restrict what can be tracked.

### 1.3 The system as a universal sink

The LLM's job in this pipeline is narrowed to exactly one thing: **read a stream-of-consciousness sentence and emit an array of `(entity, value, unit, category)` tuples** — nothing more. It does not decide where data goes, what table it lives in, or whether the tracker "exists" yet. That decision is made entirely by the deterministic backend routing layer (Section 4), preserving the same neuro-symbolic boundary principle as the CP-SAT split: **the LLM interprets language; deterministic code interprets meaning-to-storage.**

This makes the system omnivorous: it can absorb fitness logs, financial logs, mood check-ins, one-off "random thought" journal entries, and anything an individual invents for themselves, through the exact same code path, with the exact same two database tables, forever.

---

## 2. The Universal Extraction Schema (Pydantic v2)

This is the only extraction schema the system uses for conversational telemetry. It fully replaces `ExerciseDetail`, `NutritionDetail`, and `ActivityExtraction` from the original architecture doc.

```python
from pydantic import BaseModel, Field
from typing import List, Optional, Any, Literal

class TelemetryDataPoint(BaseModel):
    entity: str = Field(
        ...,
        description=(
            "The thing being tracked, in normalized snake_case "
            "(e.g., 'sleep', 'stress', 'water_intake', 'pushups', "
            "'called_mom', 'pages_read', 'random_thought'). "
            "Reuse an existing entity name whenever the user is clearly "
            "referring to something already tracked, rather than inventing "
            "a near-duplicate (e.g., always 'sleep', never 'sleep_hours' "
            "and 'hours_slept' as two different entities)."
        )
    )
    value: Any = Field(
        ...,
        description=(
            "The magnitude or state. Numeric for metrics/volumes "
            "(5.5, 8, 20), boolean for binary habits (True/False), "
            "or a short string for journal notes/states "
            "('felt overwhelmed', 'skipped')."
        )
    )
    unit: Optional[str] = Field(
        None,
        description="Unit of measurement if applicable (e.g., 'hours', '/10', 'cups', 'kg', 'minutes', 'pages')."
    )
    category: Literal["metric", "binary_habit", "volume", "journal_note"] = Field(
        ...,
        description=(
            "'metric': a continuous numeric scale (stress 1-10, mood, sleep hours). "
            "'binary_habit': a yes/no occurrence (did/didn't happen). "
            "'volume': a countable quantity of repeated units (cups, reps, pages, minutes). "
            "'journal_note': unstructured text with no numeric value — venting, "
            "observations, free-form thought."
        )
    )

class UniversalExtraction(BaseModel):
    is_telemetry: bool = Field(
        ...,
        description="True if the message contains any loggable life data. False for pure scheduling requests, small talk, or questions with nothing to log."
    )
    data_points: List[TelemetryDataPoint] = Field(
        default_factory=list,
        description="One entry per distinct trackable fact found in the message. A single sentence commonly yields multiple data points."
    )
```

Notes on design choices:
- `entity` is a free string, not an `Enum` — this is the entire point. The Enum-like closed vocabulary is `category` only, which describes *shape*, not *content*.
- `value: Any` at the LLM-facing schema layer is intentional and safe: Pydantic still validates the envelope structure and required fields; the loose typing is resolved deterministically in Section 4, never left to the LLM's discretion.
- This schema is used for **every** conversational logging turn, regardless of domain. There is no branching extraction schema by topic.

---

## 3. The LLM System Prompt Instructions

The following system prompt (or a close paraphrase preserving all constraints) is sent to the Groq-hosted model whenever a message is routed to the telemetry extraction path:

```
You are a universal life-telemetry extractor. Your only job is to read a casual,
unstructured message from the user and convert every trackable fact it contains
into a standardized list of data points. You do not decide what is "worth"
tracking beyond what the user actually said — you do not editorialize, summarize,
or add commentary.

Output strictly conforms to the UniversalExtraction schema:
- is_telemetry: true if there is anything loggable in the message, else false.
- data_points: an array of {entity, value, unit, category} tuples.

Rules:
1. Extract EVERY distinct trackable fact as its own data point. A single message
   commonly contains several.
2. Use snake_case for entity names. Reuse the user's own known trackers
   (provided in context as ACTIVE_TRACKERS) whenever the message clearly refers
   to the same thing, even if worded differently. Never create a near-duplicate
   entity name for something that already exists.
3. category must be exactly one of: metric, binary_habit, volume, journal_note.
4. If a message contains no loggable data (e.g., "what's my schedule tomorrow?"),
   return is_telemetry: false and an empty data_points array.
5. Never invent a value the user did not state or clearly imply. If a habit is
   mentioned with no magnitude ("went for a walk"), use category "binary_habit"
   with value true, or "volume" with an estimated unit only if a quantity was
   actually given.

EXAMPLES:

Input: "Slept 5 hours, stress is a solid 8 today"
Output data_points:
  [{"entity": "sleep", "value": 5, "unit": "hours", "category": "metric"},
   {"entity": "stress", "value": 8, "unit": "/10", "category": "metric"}]

Input: "Did a 20 min walk"
Output data_points:
  [{"entity": "walking", "value": 20, "unit": "minutes", "category": "volume"}]

Input: "Skipped breakfast"
Output data_points:
  [{"entity": "breakfast", "value": false, "unit": null, "category": "binary_habit"}]

Input: "Feeling weird today, hard to explain"
Output data_points:
  [{"entity": "mood_note", "value": "feeling weird, hard to explain", "unit": null, "category": "journal_note"}]

Input: "3 sets of 10 pushups, called my mom, read 15 pages before bed"
Output data_points:
  [{"entity": "pushups", "value": 30, "unit": "reps", "category": "volume"},
   {"entity": "called_mom", "value": true, "unit": null, "category": "binary_habit"},
   {"entity": "pages_read", "value": 15, "unit": "pages", "category": "volume"}]

Input: "What time is my networks exam?"
Output: {"is_telemetry": false, "data_points": []}
```

The `ACTIVE_TRACKERS` context injected alongside this prompt (a lightweight list of `{entity, category, unit}` from the user's existing `tracker_definitions`) is what allows the model to reuse prior entities instead of fragmenting the same concept into synonyms across sessions — this is the LLM-side half of the dedup strategy; the deterministic half is in Section 4.4.

---

## 4. Just-In-Time (JIT) Auto-Registration Logic

This section defines the deterministic backend flow that turns a validated `UniversalExtraction` payload into rows in the database. No LLM involvement occurs past this point.

### 4.1 High-level flow

```
Incoming message
   │
   ▼
LLM extraction → UniversalExtraction (validated by Pydantic)
   │
   ▼
For each TelemetryDataPoint in data_points:
   │
   ├─ 1. Normalize entity name (lowercase, snake_case, trim)
   ├─ 2. Look up tracker_definitions WHERE user_id = X AND name = entity
   │       │
   │       ├─ EXISTS  → use existing definition (skip to step 4)
   │       └─ NOT EXISTS → step 3 (JIT registration)
   │
   ├─ 3. Infer data_type from `value` + `category`, then INSERT a new
   │      tracker_definitions row (see 4.2)
   │
   └─ 4. INSERT a row into telemetry_logs referencing this entity,
          with the raw value stored in metadata JSONB (see 4.3)
   │
   ▼
Return a confirmation payload to the response synthesizer (Section 5)
```

### 4.2 Data-type inference rules (JIT registration)

When no `tracker_definitions` row exists for `entity`, one is created automatically using this inference table:

| `category` | Python type of `value` | Inferred `data_type` | `val_min` / `val_max` | `unit` |
|---|---|---|---|---|
| `metric` | `float` or `int` | `float` | Left `NULL` unless the LLM-supplied `unit` is a recognizable bounded scale (e.g. `/10` → 0–10); otherwise inferred conservatively from observed range on first few entries | from payload `unit`, or `NULL` |
| `binary_habit` | `bool` | `boolean` | `NULL` | `NULL` |
| `volume` | `float` or `int` | `float` | `NULL` | from payload `unit` |
| `journal_note` | `str` | `text` | `NULL` | `NULL` |

Registration statement (conceptual — implemented via SQLAlchemy upsert):

```python
async def get_or_create_tracker(db, user_id: str, dp: TelemetryDataPoint) -> TrackerDefinition:
    entity = dp.entity.strip().lower().replace(" ", "_")

    existing = await db.execute(
        select(TrackerDefinition).where(
            TrackerDefinition.user_id == user_id,
            TrackerDefinition.name == entity,
        )
    )
    tracker = existing.scalar_one_or_none()
    if tracker:
        return tracker

    inferred_type = infer_data_type(dp.category, dp.value)
    new_tracker = TrackerDefinition(
        user_id=user_id,
        name=entity,
        category=dp.category,
        data_type=inferred_type,
        unit=dp.unit,
        val_min=None,
        val_max=None,
    )
    db.add(new_tracker)
    await db.flush()   # get generated id without a full commit yet
    return new_tracker


def infer_data_type(category: str, value) -> str:
    if category == "binary_habit":
        return "boolean"
    if category == "journal_note":
        return "text"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "float"
    return "text"
```

This is the "UPSERT" behavior the directive requires: lookup-or-create, atomic within the same request/transaction, with zero manual schema migration and zero developer intervention for a never-before-seen `entity`.

### 4.3 Writing the telemetry log

Once a `TrackerDefinition` is guaranteed to exist (either pre-existing or just JIT-registered), the raw event is appended to `telemetry_logs` — the same append-only table defined in `ARCHITECTURE.md`, unchanged:

```python
async def write_telemetry(db, user_id: str, raw_message: str, dp: TelemetryDataPoint, tracker: TrackerDefinition):
    entry = TelemetryLog(
        user_id=user_id,
        entry_type=dp.category,
        content=raw_message,
        metadata={
            "tracker_name": tracker.name,
            "value": dp.value,
            "unit": dp.unit,
        },
    )
    db.add(entry)
```

The full per-message transaction (all data points processed, then a single commit) ensures partial failures don't leave orphaned tracker definitions without corresponding logs.

### 4.4 Entity de-duplication safeguard

Because the LLM is given `ACTIVE_TRACKERS` as context (Section 3) but can still occasionally drift (e.g., emitting `sleep_hours` when `sleep` already exists), the backend applies a lightweight fuzzy-match safety net before falling through to JIT creation:

1. Exact match on normalized `entity` name (primary path).
2. If no exact match, check trigram/Levenshtein similarity (e.g., Postgres `pg_trgm`) against existing tracker names for this user above a high similarity threshold (~0.85).
3. If a high-similarity match is found, route to that existing tracker instead of creating a new one, and log the near-miss for later prompt-tuning review.
4. Only if both checks fail does true JIT registration (4.2) occur.

This keeps the system honest to "zero schema updates" while preventing silent tracker fragmentation over long-term use.

### 4.5 Habit-completion & streak side-effects

For any data point with `category = "binary_habit"` and `value = true`, the same write also triggers the existing streak-increment logic against that tracker's history in `telemetry_logs` (a rolling `COUNT`/consecutive-day query — no schema change needed, since `telemetry_logs` already carries `logged_date`). This preserves the audio feedback trigger (`playSuccessJingle()`) from `ARCHITECTURE.md` without any special-casing per habit type.

---

## 5. Dynamic Conversational Feedback

The response synthesizer (a second, lightweight LLM call, or a templated formatter — either is acceptable) converts the list of `(tracker, value, unit)` results from Section 4 into one short confirmation sentence, echoing back exactly what was understood so the user can immediately catch and correct any misparse. This is the trust-building mechanism for a system with no visible form or checkbox to confirm against.

### 5.1 Formatting rules

- List entities in the order they were extracted.
- Format each as `entity_display_name (value unit)` — using the tracker's *display* form (title-cased, spaces instead of underscores), not the raw snake_case storage key.
- Boolean `false` values render as a plain negative statement ("breakfast: skipped") rather than "breakfast (False)".
- `journal_note` entries are counted, not echoed verbatim, to keep the confirmation short: "...and 1 note."
- Newly-created trackers get a one-time additional clause on their *first* log only: "(started tracking this)" — so the user is aware a new tracker was born, without it becoming noise on every subsequent mention.

### 5.2 Example

Given the extraction from Section 3's last example:

```json
[{"entity": "pushups", "value": 30, "unit": "reps", "category": "volume"},
 {"entity": "called_mom", "value": true, "unit": null, "category": "binary_habit"},
 {"entity": "pages_read", "value": 15, "unit": "pages", "category": "volume"}]
```

Synthesized reply:

> "Logged: Pushups (30 reps), Called Mom ✓, Pages Read (15 pages)."

Given a mixed example with a skip and a new tracker:

```json
[{"entity": "sleep", "value": 5.5, "unit": "hours", "category": "metric"},
 {"entity": "stress", "value": 8, "unit": "/10", "category": "metric"},
 {"entity": "breakfast", "value": false, "unit": null, "category": "binary_habit"},
 {"entity": "smiled_at_strangers", "value": true, "unit": null, "category": "binary_habit"}]
```

Synthesized reply (with `smiled_at_strangers` being a first-time tracker):

> "Logged: Sleep (5.5 hours), Stress (8/10), Breakfast: skipped, Smiled At Strangers ✓ (started tracking this)."

### 5.3 Why this matters

Because there is no UI checkbox confirming a log succeeded, this echo is the entire feedback loop for correctness. It must always fire on any `is_telemetry: true` result, immediately following the write in Section 4, and must complete before (or alongside) the audio feedback cue defined in `ARCHITECTURE.md` — the sound reinforces that logging happened; the sentence confirms *what* was logged.

---

## 6. Migration Notes (Relative to Original ARCHITECTURE.md)

- **Remove**: `ExerciseLog`, `NutritionLog`, `ExerciseDetail`, `NutritionDetail`, `ActivityExtraction`, `ConversationalUpdate` schemas and any FastAPI routes/prompts referencing them by name.
- **Remove**: any hardcoded `activity_logs` fitness/nutrition-specific table or column set, if already scaffolded — all telemetry now flows through the existing generic `tracker_definitions` + `telemetry_logs` pair.
- **Keep unchanged**: `schedule_items`, `semantic_contexts`, `user_profiles`, the CP-SAT solver, the syllabus RAG pipeline, the episodic memory pipeline, and the Web Audio feedback engine — none of these are affected by this refactor.
- **New dependency**: Postgres `pg_trgm` extension, for the fuzzy-match de-duplication safeguard in Section 4.4.
- **New backend module**: a single `telemetry_engine.py` (or equivalent) implementing Sections 4 and 5, replacing any per-domain extraction handlers.
