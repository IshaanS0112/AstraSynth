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

CREATE INDEX idx_terrain_analyses_mission ON terrain_analyses(mission_id);
CREATE INDEX idx_rover_paths_mission      ON rover_paths(mission_id);
CREATE INDEX idx_risk_reports_mission     ON mission_risk_reports(mission_id);
CREATE INDEX idx_risk_reports_path        ON mission_risk_reports(rover_path_id);
