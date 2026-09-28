-- ====================================================================
-- Autonomous Cognitive Offloader — Exact Database Schema
-- Per ARCHITECTURE.md Section 5
-- ====================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Master profile & global scheduling parameters
CREATE TABLE IF NOT EXISTS user_profiles (
  user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email VARCHAR(255) UNIQUE NOT NULL,
  sleep_start TIME NOT NULL DEFAULT '23:00:00',
  sleep_end TIME NOT NULL DEFAULT '07:00:00',
  buffer_minutes INT NOT NULL DEFAULT 15,
  max_study_hours_per_day INT NOT NULL DEFAULT 8,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Syllabus RAG + episodic life-nuance vectors, kept in one table but tagged
CREATE TABLE IF NOT EXISTS semantic_contexts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES user_profiles(user_id) ON DELETE CASCADE,
  context_type VARCHAR(30) NOT NULL, -- 'syllabus_module' | 'episodic_constraint' | 'life_habit' | 'telemetry_entry' | 'project_goal'
  subject VARCHAR(100),
  raw_content TEXT NOT NULL,
  embedding VECTOR(1536),
  metadata JSONB DEFAULT '{}'::jsonb,
  source_table VARCHAR(40), -- 'schedule_items' | 'tracker_definitions' | 'telemetry_logs' | 'upload' | 'conversation'
  source_id UUID,
  content_hash CHAR(64), -- SHA-256 hex digest
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  CONSTRAINT uq_semantic_context_provenance UNIQUE(user_id, source_table, source_id)
);
CREATE INDEX IF NOT EXISTS idx_semantic_embedding ON semantic_contexts USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_semantic_provenance ON semantic_contexts (user_id, source_table, source_id);

-- Omnivorous Knowledge Graph Context Edges
CREATE TABLE IF NOT EXISTS context_edges (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES user_profiles(user_id) ON DELETE CASCADE,
  source_id UUID REFERENCES semantic_contexts(id) ON DELETE CASCADE,
  target_id UUID REFERENCES semantic_contexts(id) ON DELETE CASCADE,
  kind VARCHAR(20) NOT NULL DEFAULT 'semantic', -- 'semantic' | 'structural'
  weight FLOAT NOT NULL DEFAULT 1.0,
  cross_domain BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  CONSTRAINT ck_canonical_edge_order CHECK (source_id < target_id),
  CONSTRAINT uq_context_edge UNIQUE(user_id, source_id, target_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_context_edges_user ON context_edges (user_id);
CREATE INDEX IF NOT EXISTS idx_context_edges_source ON context_edges (source_id);
CREATE INDEX IF NOT EXISTS idx_context_edges_target ON context_edges (target_id);

-- Unified calendar, exclusively CP-SAT managed
CREATE TABLE IF NOT EXISTS schedule_items (
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
CREATE INDEX IF NOT EXISTS idx_schedule_user_dates ON schedule_items (user_id, start_time, end_time);

-- Dynamic tracker registry (EAV)
CREATE TABLE IF NOT EXISTS tracker_definitions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES user_profiles(user_id) ON DELETE CASCADE,
  name VARCHAR(100) NOT NULL,
  category VARCHAR(50) NOT NULL, -- 'metric' | 'binary_habit' | 'volume' | 'journal_note'
  data_type VARCHAR(20) NOT NULL, -- 'float' | 'boolean' | 'text'
  val_min FLOAT,
  val_max FLOAT,
  unit VARCHAR(30),
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Raw telemetry event stream (the digital rapid log)
CREATE TABLE IF NOT EXISTS telemetry_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES user_profiles(user_id) ON DELETE CASCADE,
  entry_type VARCHAR(20) NOT NULL, -- 'task' | 'event' | 'note' | 'metric' | 'memory'
  content TEXT,
  metadata JSONB DEFAULT '{}'::jsonb,
  logged_date DATE NOT NULL DEFAULT CURRENT_DATE,
  logged_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_telemetry_user_date ON telemetry_logs (user_id, logged_date);
CREATE INDEX IF NOT EXISTS idx_telemetry_gin ON telemetry_logs USING gin (metadata);
