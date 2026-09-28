# Autonomous Cognitive Offloader

> **Neuro-Symbolic Life Scheduler & Conversational BuJo Logger**  
> *Deterministic Google OR-Tools CP-SAT math engine paired with Groq Socratic LLM extraction.*

---

## 1. Architectural Philosophy (The Hard Boundary)

- **The LLM (Groq Cloud)** is **ONLY** an interviewer, extractor, and synthesizer. It parses messy human conversation into typed, Pydantic-validated JSON. It **never** writes or decides `start_time` / `end_time` or performs calendar arithmetic.
- **Google OR-Tools CP-SAT** is the **ONLY** component that places, moves, or resolves calendar time slots. Discretized into 15-minute ticks over a rolling 7-day window (672 ticks), it mathematically guarantees zero overlaps via `AddNoOverlap` and enforces priority-weighted preemption.

---

## 2. Key Modules & Capabilities

1. **Socratic Goal Interrogation (Two-Pass Loop)**:
   - **Pass 1**: Vague goals ("I want to start studying Networks more") trigger ambiguity detection, returning up to 2 sharp clarifying questions without touching the schedule.
   - **Pass 2**: Clarified inputs emit structured parameters directly to the CP-SAT solver.
2. **Sudden Exam Preemption & Warning Sound**:
   - Priority 10 exam arrival automatically triggers CP-SAT re-solve, pre-empting lower-priority discretionary tasks and playing the Web Audio descending **Sad Trombone**.
3. **Conversational BuJo Telemetry (Zero-Form Rapid Logging)**:
   - Casual descriptions ("did 3x10 pushups, 20kg deadlifts, ate a protein bar") silently populate `telemetry_logs`, dynamically register tracker definitions, increment streaks, and play the Web Audio ascending **Dopamine Jingle**.
4. **Dual-Memory Context Engine ("Never Re-Explain")**:
   - **Academic Syllabus RAG**: PyMuPDF extraction, 350-word chunking, and 1536-dim vector embeddings (`syllabus_module`).
   - **Episodic Life Constraints**: Personal health/schedule guardrails (`episodic_constraint`, e.g., "No screen work past 10 PM gives me migraines") injected into prompts to push back on conflicting requests.
5. **Socratic Migration Weeding**:
   - Ryder Carroll BuJo rule: If a task migrates forward $\ge 3$ times, the agent stops auto-rescheduling and asks the user to commit or strike it.
6. **"The Ledger" Dashboard**:
   - Notion × Newsprint × Bullet Journal design system with dot-grid paper texture, zero border radius everywhere, hard offset hover shadows, Recharts area charts, stat cards with inverted Newsprint accent, and reverse-chronological BuJo stream with authentic glyphs (`·`, `X`, `O`, `—`, `∷`, `!`, `>`, `△`).

---

## 3. Quickstart & Running

### Backend (FastAPI + CP-SAT + Groq + PyMuPDF)

```bash
cd backend
python -m venv venv
# Windows: venv\Scripts\activate | Unix: source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```
Backend runs at `http://localhost:8000`.
- Interactive Web UI: `http://localhost:8000/app` or `http://localhost:8000/` (in browser)
- Interactive OpenAPI Docs: `http://localhost:8000/docs`

### Frontend (Next.js 14 App Router)

```bash
cd frontend
npm install
npm run dev
```
Frontend runs at `http://localhost:3000`.

---

## 4. Running the Test Suite

```bash
cd backend
pytest -v
```

The test suite covers:
- `test_solver.py`: 15-minute discretization, non-overlap, sleep window defense, priority-10 preemption over priority-2.
- `test_database.py`: SQLAlchemy models, pgvector / SQLite compatibility, EAV tracker definitions, BuJo telemetry stream.
- `test_phase2_phase3.py`: Socratic two-pass loop, zero-form telemetry extraction, PyMuPDF syllabus ingestion, episodic constraint guardrail pushback, Ryder Carroll migration weed.
- `test_demo_day_scenarios.py`: End-to-end simulation of all 3 PRD demo scenarios and web UI endpoints.
