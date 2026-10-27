import { ArrowRight, Plus, Search, X } from "lucide-react";
import { useMemo } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import type { MissionStatus } from "../api/types";
import { dateTime, relativeTime } from "../lib/format";
import { useAsync } from "../lib/useAsync";
import Badge from "../ui/Badge";
import { StageBar } from "../ui/Bars";
import PageHeader from "../ui/PageHeader";
import ReliefImage from "../ui/ReliefImage";
import { EmptyState, ErrorState, TableSkeleton } from "../ui/States";
import { MISSION_STAGES, MISSION_STATUS } from "../ui/status";

type Sort = "newest" | "oldest" | "name";

export default function Missions() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const query = params.get("q") ?? "";
  const status = (params.get("status") ?? "") as MissionStatus | "";
  const sort = (params.get("sort") ?? "newest") as Sort;
  const { data, error, loading, reload } = useAsync(() => api.listMissions(), []);

  // Filters live in the URL, so a filtered list can be linked and survives a reload.
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const missions = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const list = (data ?? []).filter(
      (mission) =>
        (!status || mission.status === status) &&
        (!needle || `${mission.name} ${mission.terrain_source ?? ""}`.toLowerCase().includes(needle)),
    );
    return list.sort((a, b) =>
      sort === "name"
        ? a.name.localeCompare(b.name)
        : sort === "oldest"
          ? a.created_at.localeCompare(b.created_at)
          : b.created_at.localeCompare(a.created_at),
    );
  }, [data, query, status, sort]);

  const filtered = Boolean(query || status);

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 lg:px-8 lg:py-8">
      <PageHeader
        title="Missions"
        description="One mission per terrain tile: analysis, planned paths, simulated traverses and studies."
        actions={
          <Link to="/missions/new" className="btn-primary">
            <Plus className="h-4 w-4" strokeWidth={1.5} aria-hidden="true" />
            New mission
          </Link>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="relative w-full sm:w-auto sm:min-w-0 sm:max-w-xs sm:flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle" strokeWidth={1.5} aria-hidden="true" />
          <input
            className="field pl-9"
            placeholder="Search name or terrain source"
            aria-label="Search missions"
            value={query}
            onChange={(event) => set("q", event.target.value)}
          />
        </div>
        <select className="field w-auto" aria-label="Filter by status" value={status} onChange={(event) => set("status", event.target.value)}>
          <option value="">All statuses</option>
          {MISSION_STAGES.map((stage) => (
            <option key={stage} value={stage}>
              {MISSION_STATUS[stage].label}
            </option>
          ))}
        </select>
        <select className="field w-auto" aria-label="Sort missions" value={sort} onChange={(event) => set("sort", event.target.value === "newest" ? "" : event.target.value)}>
          <option value="newest">Newest first</option>
          <option value="oldest">Oldest first</option>
          <option value="name">Name A–Z</option>
        </select>
        {filtered && (
          <button className="btn h-10 px-3 text-muted hover:text-fg lg:h-9" onClick={() => setParams({}, { replace: true })}>
            <X className="h-4 w-4" strokeWidth={1.5} aria-hidden="true" />
            Clear
          </button>
        )}
        {data && (
          <span className="ml-auto font-mono text-2xs uppercase tracking-wider text-subtle">
            {missions.length} of {data.length}
          </span>
        )}
      </div>

      {error && !data ? (
        <ErrorState error={error} onRetry={reload} title="Could not load missions" />
      ) : loading && !data ? (
        <div className="panel overflow-hidden">
          <TableSkeleton rows={6} cols={5} />
        </div>
      ) : data && data.length === 0 ? (
        <EmptyState
          title="No missions yet"
          body="Upload a grayscale DEM to create one. Synthetic sample tiles are in data/sample_terrain/, and scripts/prepare_usgs_terrain.py cuts real USGS Mars tiles."
          action={
            <Link to="/missions/new" className="btn-primary">
              New mission
            </Link>
          }
        />
      ) : missions.length === 0 ? (
        <EmptyState
          compact
          title="No missions match"
          body="Nothing has that name, source or status."
          action={
            <button className="btn-ghost" onClick={() => setParams({}, { replace: true })}>
              Clear filters
            </button>
          }
        />
      ) : (
        <>
          {/* A table from md up; cards on a phone, where columns would not fit. */}
          <div className="panel hidden overflow-hidden md:block">
            <table className="tbl">
              <thead>
                <tr>
                  <th className="w-14" aria-label="Terrain" />
                  <th>Mission</th>
                  <th>Status</th>
                  <th className="w-40">Pipeline</th>
                  <th className="text-right">Created</th>
                  <th className="w-44 text-right" aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {missions.map((mission) => {
                  const ready = mission.status !== "PENDING";
                  return (
                    <tr key={mission.id} className="cursor-pointer" onClick={() => navigate(`/missions/${mission.id}`)}>
                      <td className="py-1.5">
                        <div className="h-9 w-9 overflow-hidden rounded-md border border-line bg-surface-2">
                          <ReliefImage src={mission.terrain_image_url} className="h-full w-full" />
                        </div>
                      </td>
                      <td className="max-w-80">
                        <Link
                          to={`/missions/${mission.id}`}
                          onClick={(event) => event.stopPropagation()}
                          className="block truncate font-medium hover:text-accent"
                        >
                          {mission.name}
                        </Link>
                        <div className="truncate text-xs text-muted">{mission.terrain_source || "Source unspecified"}</div>
                      </td>
                      <td>
                        <Badge tone={MISSION_STATUS[mission.status]?.tone ?? "neutral"}>
                          {MISSION_STATUS[mission.status]?.label ?? mission.status}
                        </Badge>
                      </td>
                      <td>
                        <StageBar status={mission.status} />
                      </td>
                      <td className="whitespace-nowrap text-right text-xs text-muted" title={dateTime(mission.created_at)}>
                        {relativeTime(mission.created_at)}
                      </td>
                      <td className="text-right">
                        {ready ? (
                          <Link
                            to={`/missions/${mission.id}/control`}
                            onClick={(event) => event.stopPropagation()}
                            className="btn-ghost h-8 px-3 text-xs lg:h-8"
                          >
                            Mission control
                            <ArrowRight className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden="true" />
                          </Link>
                        ) : (
                          <span className="text-xs text-subtle">Analyse first</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <ul className="space-y-2 md:hidden">
            {missions.map((mission) => (
              <li key={mission.id}>
                <Link to={`/missions/${mission.id}`} className="panel flex items-center gap-3 p-3">
                  <div className="h-12 w-12 shrink-0 overflow-hidden rounded-md border border-line bg-surface-2">
                    <ReliefImage src={mission.terrain_image_url} className="h-full w-full" />
                  </div>
                  <div className="min-w-0 flex-1 space-y-1.5">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-medium text-fg">{mission.name}</span>
                      <span className="shrink-0 text-2xs text-muted">{relativeTime(mission.created_at)}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge tone={MISSION_STATUS[mission.status]?.tone ?? "neutral"}>
                        {MISSION_STATUS[mission.status]?.label ?? mission.status}
                      </Badge>
                      <StageBar status={mission.status} className="flex-1" />
                    </div>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
