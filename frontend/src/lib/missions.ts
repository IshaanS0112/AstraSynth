import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { ExperimentSummary, JobSummary, Mission, TraverseRunSummary } from "../api/types";
import { mapLimit } from "./useAsync";

const names = new Map<string, Promise<string | null>>();

/** A mission's name for breadcrumbs, fetched once per session. */
export function useMissionName(id: string | undefined): string | null {
  const [name, setName] = useState<string | null>(null);
  useEffect(() => {
    if (!id) {
      setName(null);
      return;
    }
    let cancelled = false;
    if (!names.has(id)) {
      names.set(
        id,
        api.getMission(id).then(
          (mission) => mission.name,
          () => null,
        ),
      );
    }
    names.get(id)!.then((value) => {
      if (!cancelled) setName(value);
    });
    return () => {
      cancelled = true;
    };
  }, [id]);
  return name;
}

/** Record a name already in hand, so its breadcrumb needs no fetch. */
export function rememberMissionName(mission: Mission): void {
  names.set(mission.id, Promise.resolve(mission.name));
}

export interface MissionActivity {
  mission: Mission;
  traverses: TraverseRunSummary[];
  experiments: ExperimentSummary[];
  jobs: JobSummary[];
}

/**
 * Everything that has happened, mission by mission. The API is per mission, so
 * this fans out a few requests at a time rather than all at once.
 */
export async function loadActivity(
  missions: Mission[],
  include: { traverses?: boolean; experiments?: boolean; jobs?: boolean } = {
    traverses: true,
    experiments: true,
    jobs: true,
  },
): Promise<MissionActivity[]> {
  const none = <T,>(): Promise<T[]> => Promise.resolve([]);
  return mapLimit(missions, 4, async (mission) => {
    // A mission that has never been analysed has no runs; skip the round trips.
    const fresh = mission.status === "PENDING";
    const [traverses, experiments, jobs] = await Promise.all([
      include.traverses && !fresh ? api.listTraverses(mission.id) : none<TraverseRunSummary>(),
      include.experiments && !fresh ? api.listExperiments(mission.id) : none<ExperimentSummary>(),
      include.jobs && !fresh ? api.listMissionJobs(mission.id) : none<JobSummary>(),
    ]);
    return { mission, traverses, experiments, jobs };
  });
}
