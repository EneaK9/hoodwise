CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- Vehicle catalog
-- ---------------------------------------------------------------------------
CREATE TABLE vehicles (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  make          TEXT NOT NULL,
  model         TEXT NOT NULL,
  generation    TEXT NOT NULL,
  chassis       TEXT NOT NULL,
  year_from     INT NOT NULL,
  year_to       INT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (make, model, generation, chassis)
);

CREATE TABLE vehicle_variants (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  vehicle_id      UUID NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
  engine_code     TEXT,
  engine_label    TEXT NOT NULL,
  displacement_l  NUMERIC(3,1),
  turbo           BOOLEAN NOT NULL DEFAULT false,
  transmission    TEXT NOT NULL,
  trim            TEXT,
  body            TEXT,
  market          TEXT,
  year_from       INT,
  year_to         INT,
  notes           TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX vehicle_variants_vehicle_idx ON vehicle_variants(vehicle_id);

-- ---------------------------------------------------------------------------
-- Manual content
-- ---------------------------------------------------------------------------
CREATE TABLE documents (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  vehicle_id    UUID REFERENCES vehicles(id) ON DELETE SET NULL,
  doc_id        TEXT NOT NULL UNIQUE,
  filename      TEXT NOT NULL,
  section_name  TEXT NOT NULL,
  page_count    INT,
  file_hash     TEXT,
  status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'extracted', 'embedded', 'failed')),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE pages (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  page_number     INT NOT NULL,
  text_content    TEXT,
  char_count      INT NOT NULL DEFAULT 0,
  has_text_layer  BOOLEAN NOT NULL DEFAULT true,
  render_path     TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (document_id, page_number)
);

CREATE TABLE chunks (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id       UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  page_id           UUID REFERENCES pages(id) ON DELETE SET NULL,
  page_number       INT NOT NULL,
  section_path      TEXT,
  chunk_type        TEXT NOT NULL
                      CHECK (chunk_type IN ('prose', 'diagram_caption', 'procedure_step')),
  content           TEXT NOT NULL,
  embedding         vector(1536),
  embedding_model   TEXT,
  tsv               tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX chunks_tsv_idx ON chunks USING GIN (tsv);
CREATE INDEX chunks_doc_page_idx ON chunks(document_id, page_number);
CREATE INDEX chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE diagrams (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id       UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  page_number       INT NOT NULL,
  image_path        TEXT NOT NULL,
  bbox              JSONB,
  caption           TEXT,
  classification    TEXT
                      CHECK (classification IN ('diagram', 'spec_callout', 'table', 'decorative', 'unknown')),
  phash             TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX diagrams_phash_idx ON diagrams(phash);
CREATE INDEX diagrams_doc_page_idx ON diagrams(document_id, page_number);

CREATE TABLE specs (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id           UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  variant_id            UUID REFERENCES vehicle_variants(id) ON DELETE SET NULL,
  page_number           INT NOT NULL,
  part_name             TEXT NOT NULL,
  spec_type             TEXT NOT NULL,
  value_nm              NUMERIC,
  value_raw             TEXT NOT NULL,
  unit                  TEXT,
  torque_sequence       TEXT,
  replace_required      BOOLEAN NOT NULL DEFAULT false,
  condition_note        TEXT,
  raw_context           TEXT,
  bbox                  JSONB,
  crop_path             TEXT,
  diagram_id            UUID REFERENCES diagrams(id) ON DELETE SET NULL,
  source                TEXT NOT NULL DEFAULT 'text'
                          CHECK (source IN ('text', 'vision', 'merged')),
  confidence            NUMERIC(4,3),
  verification_status   TEXT NOT NULL DEFAULT 'auto_agreed'
                          CHECK (verification_status IN ('auto_agreed', 'flagged', 'human_verified')),
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX specs_part_idx ON specs USING GIN (to_tsvector('english', part_name));
CREATE INDEX specs_type_idx ON specs(spec_type);
CREATE INDEX specs_variant_idx ON specs(variant_id);

-- ---------------------------------------------------------------------------
-- VIN
-- ---------------------------------------------------------------------------
CREATE TABLE vin_decodes (
  vin             TEXT PRIMARY KEY,
  raw_response    JSONB NOT NULL,
  variant_id      UUID REFERENCES vehicle_variants(id) ON DELETE SET NULL,
  source          TEXT NOT NULL
                    CHECK (source IN ('vpic', 'pattern', 'wmi', 'manual', 'vincario', 'carsxe', 'api_ninjas')),
  confidence      TEXT NOT NULL DEFAULT 'partial'
                    CHECK (confidence IN ('full', 'partial', 'wmi_only')),
  decoded_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Auth + garage
-- ---------------------------------------------------------------------------
CREATE TABLE users (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email           TEXT NOT NULL UNIQUE,
  password_hash   TEXT NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE auth_sessions (
  token_hash      TEXT PRIMARY KEY,
  user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at      TIMESTAMPTZ NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  user_agent      TEXT
);

CREATE INDEX auth_sessions_user_idx ON auth_sessions(user_id);
CREATE INDEX auth_sessions_expires_idx ON auth_sessions(expires_at);

CREATE TABLE user_vehicles (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  variant_id      UUID NOT NULL REFERENCES vehicle_variants(id) ON DELETE CASCADE,
  vin             TEXT,
  nickname        TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX user_vehicles_user_idx ON user_vehicles(user_id);

-- ---------------------------------------------------------------------------
-- Pipeline
-- ---------------------------------------------------------------------------
CREATE TABLE ingestion_runs (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id   UUID REFERENCES documents(id) ON DELETE CASCADE,
  stage         TEXT NOT NULL,
  status        TEXT NOT NULL
                  CHECK (status IN ('running', 'ok', 'failed')),
  started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at   TIMESTAMPTZ,
  error         TEXT
);

CREATE TABLE extraction_calls (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id        UUID REFERENCES ingestion_runs(id) ON DELETE CASCADE,
  page_number   INT,
  model         TEXT NOT NULL,
  purpose       TEXT NOT NULL,
  tokens_in     INT,
  tokens_out    INT,
  cost_usd      NUMERIC(10,6),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Chat
-- ---------------------------------------------------------------------------
CREATE TABLE chat_sessions (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID REFERENCES users(id) ON DELETE SET NULL,
  variant_id    UUID REFERENCES vehicle_variants(id) ON DELETE SET NULL,
  vin           TEXT,
  title         TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX chat_sessions_user_idx ON chat_sessions(user_id);

CREATE TABLE messages (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id    UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  role          TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
  content       TEXT NOT NULL,
  model         TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX messages_session_idx ON messages(session_id, created_at);

CREATE TABLE message_citations (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  message_id    UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  chunk_id      UUID REFERENCES chunks(id) ON DELETE SET NULL,
  spec_id       UUID REFERENCES specs(id) ON DELETE SET NULL,
  diagram_id    UUID REFERENCES diagrams(id) ON DELETE SET NULL,
  rank          INT NOT NULL DEFAULT 0,
  similarity    NUMERIC
);

CREATE TABLE retrieval_logs (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  message_id      UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  query           TEXT NOT NULL,
  retrieval_mode  TEXT NOT NULL,
  candidate_ids   JSONB NOT NULL DEFAULT '[]',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Eval
-- ---------------------------------------------------------------------------
CREATE TABLE eval_questions (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  question              TEXT NOT NULL,
  category              TEXT NOT NULL,
  expected_part         TEXT,
  expected_value_raw    TEXT,
  expected_substring    TEXT,
  variant_hint          TEXT,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE eval_runs (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  git_sha       TEXT,
  config        JSONB,
  started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at   TIMESTAMPTZ
);

CREATE TABLE eval_results (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id          UUID NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
  question_id     UUID NOT NULL REFERENCES eval_questions(id) ON DELETE CASCADE,
  passed          BOOLEAN NOT NULL,
  retrieved_ids   JSONB,
  answer_text     TEXT,
  notes           TEXT
);
