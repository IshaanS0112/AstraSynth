import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { Mission } from "../api/types";

const STATUS_STYLES: Record<string, string> = {
  PENDING: "border-slate-600 text-slate-400",
  ANALYZED: "border-sky-500/40 text-sky-300",
  PATH_PLANNED: "border-indigo-500/40 text-indigo-300",
  RISK_ASSESSED: "border-amber-500/40 text-amber-300",
  REPORT_GENERATED: "border-teal-500/40 text-teal-300",
};

export default function Dashboard() {
  const [missions, setMissions] = useState<Mission[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listMissions()
      .then(setMissions)
      .catch((caught) => setError(String(caught.message ?? caught)));
  }, []);

  return (
    <div className="mx-auto max-w-7xl px-6 py-6">
      <div className="mb-5 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Missions</h1>
          <p className="text-xs text-slate-500">
            Terrain hazard analysis, energy-aware A* traverse planning, battery feasibility.
          </p>
        </div>
        <Link to="/missions/new" className="btn-primary">
          New mission
        </Link>
      </div>

      {error && (
        <div className="rounded border border-rose-500/40 bg-rose-500/10 px-4 py-2 text-sm text-rose-300">
          {error}
        </div>
      )}

      {missions && missions.length === 0 && (
        <div className="panel p-10 text-center">
          <p className="text-sm text-slate-400">No missions yet.</p>
          <p className="mt-1 text-xs text-slate-500">
            Upload a terrain image to run the analysis pipeline. Sample tiles are in
            <span className="font-mono"> data/sample_terrain/</span>.
          </p>
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {missions?.map((mission) => (
          <Link
            key={mission.id}
            to={`/missions/${mission.id}`}
            className="panel overflow-hidden transition hover:border-accent"
          >
            {mission.terrain_image_url && (
              <img
                src={mission.terrain_image_url}
                alt=""
                className="h-32 w-full object-cover opacity-70"
              />
            )}
            <div className="p-3">
              <div className="flex items-start justify-between gap-2">
                <h2 className="text-sm font-medium text-slate-100">{mission.name}</h2>
                <span
                  className={`shrink-0 rounded border px-1.5 py-0.5 text-[9px] uppercase tracking-wider ${
                    STATUS_STYLES[mission.status] ?? "border-edge text-slate-400"
                  }`}
                >
                  {mission.status.replace(/_/g, " ")}
                </span>
              </div>
              <p className="mt-1 text-xs text-slate-500">
                {mission.terrain_source || "source unspecified"} ·{" "}
                {new Date(mission.created_at).toLocaleDateString()}
              </p>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
