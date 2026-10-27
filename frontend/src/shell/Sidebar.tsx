import { BookOpen, ExternalLink, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { Link, NavLink } from "react-router-dom";

import { BrandMark } from "../ui/icons";
import { DOCS_URL, NAV } from "./nav";

/**
 * How the sidebar lays out: ``full`` always shows labels (the phone sheet),
 * ``rail`` is icons only (mission control, or collapsed by the operator), and
 * ``responsive`` is a rail below lg and full from lg up.
 */
export type SidebarMode = "full" | "rail" | "responsive";

function pick(mode: SidebarMode, classes: { full: string; rail: string; responsive: string }): string {
  return classes[mode];
}

export default function Sidebar({
  mode,
  onToggle,
  onNavigate,
}: {
  mode: SidebarMode;
  onToggle?: () => void;
  onNavigate?: () => void;
}) {
  const label = pick(mode, { full: "", rail: "sr-only", responsive: "sr-only lg:not-sr-only" });
  const item = pick(mode, { full: "", rail: "justify-center px-0", responsive: "justify-center px-0 lg:justify-start lg:px-3" });
  const width = pick(mode, { full: "w-64", rail: "w-16", responsive: "w-16 lg:w-60" });
  const brand = pick(mode, { full: "px-5", rail: "justify-center", responsive: "justify-center lg:justify-start lg:px-5" });
  const tip = (text: string) => (mode === "full" ? undefined : text);

  return (
    <nav
      aria-label="Primary"
      className={`flex h-full shrink-0 flex-col border-r border-line bg-surface transition-[width] duration-180 ease-enter ${width}`}
    >
      <Link
        to="/"
        onClick={onNavigate}
        aria-label="AstraSynth overview"
        className={`flex h-14 shrink-0 items-center gap-3 border-b border-line ${brand}`}
      >
        <BrandMark className="h-5 w-5 shrink-0" />
        <span className={`${label} text-base font-semibold tracking-tight text-fg`}>AstraSynth</span>
      </Link>

      <div className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto p-3">
        <div className={`${label} mb-1 px-3 font-mono text-2xs uppercase tracking-wider text-subtle`}>Operations</div>
        {NAV.map((entry) => {
          const Icon = entry.icon;
          return (
            <NavLink
              key={entry.to}
              to={entry.to}
              end={entry.end}
              onClick={onNavigate}
              title={tip(entry.label)}
              className={({ isActive }) => `nav-item ${isActive ? "nav-item-active" : ""} ${item}`}
            >
              {({ isActive }) => (
                <>
                  <Icon className={`h-5 w-5 shrink-0 ${isActive ? "text-accent" : ""}`} strokeWidth={1.5} />
                  <span className={label}>{entry.label}</span>
                </>
              )}
            </NavLink>
          );
        })}
      </div>

      <div className="flex shrink-0 flex-col gap-1 border-t border-line p-3">
        <a href={DOCS_URL} target="_blank" rel="noreferrer" title={tip("Documentation")} className={`nav-item ${item}`}>
          <BookOpen className="h-5 w-5 shrink-0" strokeWidth={1.5} aria-hidden="true" />
          <span className={`${label} flex-1`}>Documentation</span>
          <ExternalLink className={`${label} h-3.5 w-3.5 text-subtle`} strokeWidth={1.5} aria-hidden="true" />
        </a>
        {onToggle && (
          <button
            onClick={onToggle}
            aria-label={mode === "rail" ? "Expand sidebar" : "Collapse sidebar"}
            title={mode === "rail" ? "Expand sidebar" : "Collapse sidebar"}
            className={`nav-item hidden lg:flex ${item}`}
          >
            {mode === "rail" ? (
              <PanelLeftOpen className="h-5 w-5 shrink-0" strokeWidth={1.5} aria-hidden="true" />
            ) : (
              <PanelLeftClose className="h-5 w-5 shrink-0" strokeWidth={1.5} aria-hidden="true" />
            )}
            <span className={label}>Collapse</span>
          </button>
        )}
        <p className={`${label} px-3 pt-2 text-2xs leading-4 text-subtle`}>
          A planning simulation. Not flight-validated.
        </p>
      </div>
    </nav>
  );
}
