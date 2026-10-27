-- AstraSynth schema, for reference.
-- The application creates these tables via SQLAlchemy on startup
-- (app/main.py lifespan). This file documents the resulting shape and is what
-- you would hand to a DBA or feed into a migration tool.

CREATE TABLE missions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                VARCHAR(200) NOT NULL,
    terrain_image_path  VARCHAR(500) NOT NULL,
    terrain_source      VARCHAR(100),           -- provenance, shown on every report
    status              VARCHAR(30)  NOT NULL,  -- PENDING → ANALYZED → PATH_PLANNED
                                                -- → RISK_ASSESSED → REPORT_GENERATED
    created_at          TIMESTAMP DEFAULT now()
);

CREATE TABLE terrain_analyses (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id             UUID NOT NULL REFERENCES missions(id),
    slope_map_path         VARCHAR(500),
    obstacle_contours      JSONB,        -- [{id,x,y,area_px,radius_px,area_m2}]
    terrain_classification VARCHAR(50),  -- rocky_highland | sandy_plain | crater_field
    hazard_heatmap_path    VARCHAR(500),
    uncertainty_map_path   VARCHAR(500),   -- where the hazard estimate is least trusted
    -- Every CV parameter, the hazard weights, aggregate statistics, the
    -- classification evidence, and the planning-grid geometry. This is what
    -- makes a result reproducible rather than merely reported.
    analysis_metadata      JSONB,
    analyzed_at            TIMESTAMP DEFAULT now()
);

CREATE TABLE rover_configs (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                      VARCHAR(100) NOT NULL,
    battery_capacity_kwh      FLOAT NOT NULL,
    max_traversable_slope_deg FLOAT NOT NULL,   -- hard planner constraint
    energy_per_meter_kwh      FLOAT NOT NULL
);

CREATE TABLE rover_paths (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id            UUID NOT NULL REFERENCES missions(id),
    rover_config_id       UUID NOT NULL REFERENCES rover_configs(id),
    start_point           JSONB NOT NULL,   -- {x, y} in source image pixels
    end_point             JSONB NOT NULL,
    waypoints             JSONB NOT NULL,   -- per step: hazard, slope, distance, energy
    total_distance_m      FLOAT,
    total_energy_cost_kwh FLOAT,
    algorithm_used        VARCHAR(30) DEFAULT 'A_star',
    -- Nodes expanded, grid shape, cost-function constants, blocked-move counts.
    -- Lets the A* claim be checked rather than taken on trust.
    planner_metadata      JSONB,
    planned_at            TIMESTAMP DEFAULT now()
);

CREATE TABLE mission_risk_reports (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id         UUID NOT NULL REFERENCES missions(id),
    rover_path_id      UUID NOT NULL REFERENCES rover_paths(id),
    risk_score         VARCHAR(20),   -- LOW | MEDIUM | HIGH
    feasibility        VARCHAR(30),   -- FEASIBLE | FEASIBLE_WITH_MARGIN | INFEASIBLE
    -- Frozen before any LLM call. Everything the narrative is allowed to cite.
    structured_context JSONB NOT NULL,
    ai_narrative       JSONB,
    narrative_source   VARCHAR(20),   -- llm | template_fallback
    generated_at       TIMESTAMP DEFAULT now()
);

-- ============================================================================
-- V3: mission autonomy
--
-- All new tables, no altered columns, which is why the application still
-- bootstraps with create_all and there is no migration tool yet. The first
-- column that changes shape is what buys Alembic.
-- ============================================================================

CREATE TABLE science_targets (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id          UUID NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
    label               VARCHAR(60) NOT NULL,
    -- Original-image pixels, the same frame the UI clicks in and waypoints use.
    -- Planning-grid cells are derived on demand; storing them would go stale the
    -- moment the terrain is re-analysed at a different downsample scale.
    x                   INTEGER NOT NULL,
    y                   INTEGER NOT NULL,
    value               DOUBLE PRECISION NOT NULL DEFAULT 0.5,   -- 0-1, relative within a mission
    priority            VARCHAR(10) NOT NULL DEFAULT 'medium',
    observation_seconds DOUBLE PRECISION NOT NULL DEFAULT 1200,
    required_instrument VARCHAR(40),
    created_at          TIMESTAMP DEFAULT now()
);

CREATE TABLE traverse_runs (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id        UUID NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
    rover_config_id   UUID NOT NULL REFERENCES rover_configs(id),
    status            VARCHAR(20) NOT NULL,   -- SUCCESS | FAILED
    failure_mode      VARCHAR(40),            -- LOW_BATTERY | ROVER_BLOCKED | ...
    reason            VARCHAR(1000),
    start_point       JSONB NOT NULL,
    goal_point        JSONB NOT NULL,
    -- Seed, sensor model, and how the synthetic ground truth was built. Without
    -- this a run cannot be repeated, and a surprise the rover met is unfalsifiable.
    parameters        JSONB NOT NULL,
    distance_m        DOUBLE PRECISION NOT NULL DEFAULT 0,
    energy_kwh        DOUBLE PRECISION NOT NULL DEFAULT 0,
    elapsed_seconds   DOUBLE PRECISION NOT NULL DEFAULT 0,
    replans           INTEGER NOT NULL DEFAULT 0,   -- belief changed
    reroutes          INTEGER NOT NULL DEFAULT 0,   -- ...and it mattered
    repair_expansions INTEGER NOT NULL DEFAULT 0,
    -- Written once, read whole, never queried field-by-field: a table per
    -- waypoint would buy nothing and cost a join per replay.
    initial_plan      JSONB NOT NULL DEFAULT '[]',
    executed_path     JSONB NOT NULL DEFAULT '[]',
    events            JSONB NOT NULL DEFAULT '[]',
    belief_summary    JSONB NOT NULL DEFAULT '{}',
    created_at        TIMESTAMP DEFAULT now()
);

CREATE TABLE experiments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id      UUID NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
    -- route_study | monte_carlo | fleet_plan | science_tour. One table with a
    -- discriminator: the four differ entirely in payload and not at all in
    -- lifecycle, and four near-identical tables would be four places to add
    -- provenance to.
    kind            VARCHAR(30) NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'SUCCESS',
    failure_mode    VARCHAR(40),
    reason          VARCHAR(1000),
    parameters      JSONB NOT NULL,
    seed            INTEGER,
    code_revision   VARCHAR(64),   -- git rev: a result you cannot place is an anecdote
    result          JSONB NOT NULL DEFAULT '{}',
    runtime_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at      TIMESTAMP DEFAULT now()
);

CREATE INDEX idx_terrain_analyses_mission ON terrain_analyses(mission_id);
CREATE INDEX idx_rover_paths_mission      ON rover_paths(mission_id);
CREATE INDEX idx_risk_reports_mission     ON mission_risk_reports(mission_id);
CREATE INDEX idx_risk_reports_path        ON mission_risk_reports(rover_path_id);
CREATE INDEX idx_science_targets_mission  ON science_targets(mission_id);
CREATE INDEX idx_traverse_runs_mission    ON traverse_runs(mission_id);
CREATE INDEX idx_experiments_mission      ON experiments(mission_id);
CREATE INDEX idx_experiments_kind         ON experiments(mission_id, kind);
