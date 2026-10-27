import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";

import CommandPalette from "./CommandPalette";
import Sidebar, { type SidebarMode } from "./Sidebar";
import Topbar from "./Topbar";

const COLLAPSE_KEY = "astrasynth.sidebar-collapsed";

function storedCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * Sidebar, topbar and a scrolling main area. Mission control fills main edge to
 * edge and gets the sidebar as a rail, so the terrain has the width.
 */
export default function AppShell({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  const [collapsed, setCollapsed] = useState(storedCollapsed);
  const [sheet, setSheet] = useState(false);
  const [palette, setPalette] = useState(false);

  const control = pathname.endsWith("/control");
  const mode: SidebarMode = control || collapsed ? "rail" : "responsive";

  const toggle = useCallback(() => {
    setCollapsed((current) => {
      try {
        window.localStorage.setItem(COLLAPSE_KEY, current ? "0" : "1");
      } catch {
        /* private mode: the choice just does not persist */
      }
      return !current;
    });
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPalette((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => setSheet(false), [pathname]);

  useEffect(() => {
    if (!sheet) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setSheet(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sheet]);

  return (
    <div className="flex h-screen overflow-hidden bg-canvas">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-surface-2 focus:px-3 focus:py-2 focus:text-sm"
      >
        Skip to content
      </a>

      <div className="hidden md:block">
        <Sidebar mode={mode} onToggle={control ? undefined : toggle} />
      </div>

      {sheet && (
        <div className="fixed inset-0 z-40 md:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <div className="animate-fade absolute inset-0 bg-canvas/70" onClick={() => setSheet(false)} />
          <div className="animate-rise relative h-full w-64">
            <Sidebar mode="full" onNavigate={() => setSheet(false)} />
          </div>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar onMenu={() => setSheet(true)} onSearch={() => setPalette(true)} />
        <main id="main" className="min-h-0 flex-1 overflow-y-auto">
          {children}
        </main>
      </div>

      <CommandPalette open={palette} onClose={() => setPalette(false)} />
    </div>
  );
}
