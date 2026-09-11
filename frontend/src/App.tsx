import { Suspense, lazy } from "react";
import { Link, Route, Routes } from "react-router-dom";

import Dashboard from "./pages/Dashboard";
import MissionDetail from "./pages/MissionDetail";
import NewMission from "./pages/NewMission";

// Mission control pulls in three.js, which is most of the bundle. Loading it
// lazily keeps the dashboard and the classic mission pages small for everyone
// who never opens the 3-D view.
const MissionControl = lazy(() => import("./pages/MissionControl"));

export default function App() {
  return (
    <div className="min-h-screen">
      <header className="border-b border-edge bg-panel/60 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-3">
          <Link to="/" className="flex items-baseline gap-2">
            <span className="text-lg font-semibold tracking-tight text-accent">AstraSynth</span>
            <span className="text-[10px] uppercase tracking-[0.2em] text-slate-500">
              planetary autonomy
            </span>
          </Link>
          <span className="hidden text-[11px] text-slate-500 sm:block">
            Planning simulation on public terrain data — not flight-validated
          </span>
        </div>
      </header>

      <main>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/missions/new" element={<NewMission />} />
          <Route path="/missions/:missionId" element={<MissionDetail />} />
          <Route
            path="/missions/:missionId/control"
            element={
              <Suspense
                fallback={
                  <div className="p-10 text-center text-sm text-slate-500">
                    Loading mission control…
                  </div>
                }
              >
                <MissionControl />
              </Suspense>
            }
          />
          <Route
            path="*"
            element={<div className="p-10 text-center text-sm text-slate-500">Not found.</div>}
          />
        </Routes>
      </main>
    </div>
  );
}
