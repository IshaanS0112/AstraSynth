import { Suspense, lazy } from "react";
import { Link, Route, Routes } from "react-router-dom";

import Fleet from "./pages/Fleet";
import Jobs from "./pages/Jobs";
import MissionDetail from "./pages/MissionDetail";
import Missions from "./pages/Missions";
import NewMission from "./pages/NewMission";
import Overview from "./pages/Overview";
import Studies from "./pages/Studies";
import System from "./pages/System";
import AppShell from "./shell/AppShell";
import { EmptyState } from "./ui/States";
import { ToastProvider } from "./ui/Toast";

// Mission control pulls in three.js, which is most of the bundle. Loading it
// lazily keeps every other page small for anyone who never opens the 3-D view.
const MissionControl = lazy(() => import("./pages/MissionControl"));

function ConsoleLoading() {
  return (
    <div className="flex h-full min-h-96 flex-col items-center justify-center gap-3" aria-busy="true">
      <div className="h-1 w-48 overflow-hidden rounded-full bg-line-strong">
        <div className="skeleton h-full w-full" />
      </div>
      <p className="font-mono text-2xs uppercase tracking-wider text-muted">Loading mission control</p>
    </div>
  );
}

function NotFound() {
  return (
    <div className="mx-auto max-w-xl px-4 py-16">
      <EmptyState
        title="Nothing at this address"
        body="The page may have moved, or the mission was removed."
        action={
          <Link to="/" className="btn-primary">
            Back to overview
          </Link>
        }
      />
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <AppShell>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/missions" element={<Missions />} />
          <Route path="/missions/new" element={<NewMission />} />
          <Route path="/missions/:missionId" element={<MissionDetail />} />
          <Route
            path="/missions/:missionId/control"
            element={
              <Suspense fallback={<ConsoleLoading />}>
                <MissionControl />
              </Suspense>
            }
          />
          <Route path="/fleet" element={<Fleet />} />
          <Route path="/studies" element={<Studies />} />
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/system" element={<System />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </AppShell>
    </ToastProvider>
  );
}
