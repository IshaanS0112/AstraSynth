import { ChevronRight, Menu, Search } from "lucide-react";
import { Fragment } from "react";
import { Link, matchPath, useLocation } from "react-router-dom";

import { useMissionName } from "../lib/missions";
import { BrandMark } from "../ui/icons";
import { NAV } from "./nav";
import { useApiStatus } from "./useApiStatus";

interface Crumb {
  label: string;
  to?: string;
}

function useCrumbs(): Crumb[] {
  const { pathname } = useLocation();
  const mission = matchPath("/missions/:missionId/*", pathname) ?? matchPath("/missions/:missionId", pathname);
  const missionId = mission?.params.missionId !== "new" ? mission?.params.missionId : undefined;
  const name = useMissionName(missionId);

  if (pathname === "/missions/new") return [{ label: "Missions", to: "/missions" }, { label: "New mission" }];
  if (missionId) {
    const crumbs: Crumb[] = [{ label: "Missions", to: "/missions" }];
    const control = pathname.endsWith("/control");
    crumbs.push({ label: name ?? "Mission", to: control ? `/missions/${missionId}` : undefined });
    if (control) crumbs.push({ label: "Mission control" });
    return crumbs;
  }
  const page = NAV.find((entry) => (entry.end ? pathname === entry.to : pathname.startsWith(entry.to)));
  return [{ label: page?.label ?? "Not found" }];
}

export default function Topbar({ onMenu, onSearch }: { onMenu: () => void; onSearch: () => void }) {
  const crumbs = useCrumbs();
  const api = useApiStatus();
  const mac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-surface px-4 lg:px-6">
      <button
        onClick={onMenu}
        aria-label="Open navigation"
        className="-ml-1 flex h-10 w-10 items-center justify-center rounded-md text-muted transition-colors duration-120 hover:bg-surface-2 hover:text-fg md:hidden"
      >
        <Menu className="h-5 w-5" strokeWidth={1.5} aria-hidden="true" />
      </button>
      <Link to="/" aria-label="AstraSynth overview" className="md:hidden">
        <BrandMark className="h-5 w-5" />
      </Link>

      <nav aria-label="Breadcrumb" className="min-w-0 flex-1">
        <ol className="flex min-w-0 items-center gap-1.5 text-sm">
          {crumbs.map((crumb, index) => {
            const last = index === crumbs.length - 1;
            return (
              <Fragment key={`${crumb.label}-${index}`}>
                {index > 0 && <ChevronRight className="h-3.5 w-3.5 shrink-0 text-subtle" strokeWidth={1.5} aria-hidden="true" />}
                <li className={`min-w-0 ${last ? "" : "hidden sm:block"}`}>
                  {crumb.to && !last ? (
                    <Link to={crumb.to} className="block truncate text-muted transition-colors duration-120 hover:text-fg">
                      {crumb.label}
                    </Link>
                  ) : (
                    <span aria-current={last ? "page" : undefined} className="block truncate font-medium text-fg">
                      {crumb.label}
                    </span>
                  )}
                </li>
              </Fragment>
            );
          })}
        </ol>
      </nav>

      <button
        onClick={onSearch}
        aria-label="Search and commands"
        className="flex h-10 items-center gap-2 rounded-md border border-line-strong bg-surface-2 px-3 text-sm text-subtle transition-colors duration-120 hover:border-muted hover:text-muted md:h-9 md:w-64"
      >
        <Search className="h-4 w-4 shrink-0" strokeWidth={1.5} aria-hidden="true" />
        <span className="hidden flex-1 text-left md:inline">Search or jump to…</span>
        <span className="hidden items-center gap-0.5 md:flex">
          <kbd className="kbd">{mac ? "⌘" : "Ctrl"}</kbd>
          <kbd className="kbd">K</kbd>
        </span>
      </button>

      <div
        className="hidden items-center gap-2 rounded-md px-2 font-mono text-2xs uppercase tracking-wider text-muted sm:flex"
        title={
          api.state === "up"
            ? "The API answered its liveness probe. Readiness is on the System page."
            : api.state === "down"
              ? "The API did not answer its liveness probe."
              : "Checking the API"
        }
      >
        <span
          className={`h-2 w-2 rounded-full ${
            api.state === "up" ? "bg-success" : api.state === "down" ? "bg-danger" : "bg-subtle"
          }`}
          aria-hidden="true"
        />
        <span>API</span>
        <span className="num normal-case text-subtle">
          {api.state === "up" && api.latencyMs !== null ? `${Math.round(api.latencyMs)} ms` : api.state === "down" ? "down" : "…"}
        </span>
      </div>

      <span
        className="hidden h-6 items-center rounded-full border border-line-strong px-2.5 font-mono text-2xs uppercase tracking-wider text-muted lg:inline-flex"
        title="Every run here is a simulation over terrain tiles. Nothing is flight-validated."
      >
        Simulation
      </span>
    </header>
  );
}
