import { Activity, FlaskConical, LayoutDashboard, ListChecks, MapPinned } from "lucide-react";

import { RoverIcon, type Icon } from "../ui/icons";

export interface NavItem {
  to: string;
  label: string;
  /** What the page is for, shown in the command palette. */
  hint: string;
  icon: Icon;
  end?: boolean;
}

export const NAV: NavItem[] = [
  { to: "/", label: "Overview", hint: "Fleet, runs and queue at a glance", icon: LayoutDashboard, end: true },
  { to: "/missions", label: "Missions", hint: "Terrain, plans and traverses", icon: MapPinned },
  { to: "/fleet", label: "Fleet", hint: "Rover configurations the planners plan for", icon: RoverIcon },
  { to: "/studies", label: "Studies", hint: "Route, robustness, fleet and science studies", icon: FlaskConical },
  { to: "/jobs", label: "Jobs", hint: "The background queue", icon: ListChecks },
  { to: "/system", label: "System", hint: "Health, readiness and metrics", icon: Activity },
];

export const DOCS_URL = "https://github.com/IshaanS0112/AstraSynth/tree/main/docs";
