import { ArrowRight, CornerDownLeft, Plus, Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api/client";
import type { Mission } from "../api/types";
import { RoverIcon, type Icon } from "../ui/icons";
import Modal from "../ui/Modal";
import { MISSION_STATUS } from "../ui/status";
import { NAV } from "./nav";

interface Command {
  id: string;
  group: "Pages" | "Actions" | "Missions";
  label: string;
  hint?: string;
  icon: Icon;
  to: string;
  keywords?: string;
}

/** Every token of the query has to appear somewhere in the command. */
function matches(command: Command, query: string): boolean {
  const haystack = `${command.label} ${command.hint ?? ""} ${command.keywords ?? ""}`.toLowerCase();
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((token) => haystack.includes(token));
}

export default function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [missions, setMissions] = useState<Mission[]>([]);
  const list = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActive(0);
    api.listMissions().then(setMissions, () => setMissions([]));
  }, [open]);

  const commands = useMemo<Command[]>(() => {
    const pages: Command[] = NAV.map((entry) => ({
      id: `page-${entry.to}`,
      group: "Pages",
      label: entry.label,
      hint: entry.hint,
      icon: entry.icon,
      to: entry.to,
    }));
    const actions: Command[] = [
      { id: "new-mission", group: "Actions", label: "New mission", hint: "Upload a terrain tile", icon: Plus, to: "/missions/new", keywords: "create upload terrain" },
      { id: "new-rover", group: "Actions", label: "Add rover configuration", hint: "Battery, slope limit, footprint", icon: RoverIcon, to: "/fleet?new=1", keywords: "create fleet" },
      { id: "queue-study", group: "Actions", label: "Queue robustness study", hint: "Monte Carlo on the background queue", icon: Plus, to: "/jobs?new=1", keywords: "monte carlo job" },
    ];
    const missionCommands: Command[] = missions.flatMap((mission) => {
      const status = MISSION_STATUS[mission.status]?.label ?? mission.status;
      const entries: Command[] = [
        {
          id: `mission-${mission.id}`,
          group: "Missions",
          label: mission.name,
          hint: `${status} · ${mission.terrain_source || "source unspecified"}`,
          icon: ArrowRight,
          to: `/missions/${mission.id}`,
        },
      ];
      if (mission.status !== "PENDING") {
        entries.push({
          id: `control-${mission.id}`,
          group: "Missions",
          label: `${mission.name} · Mission control`,
          hint: "3-D console: plan, simulate, study",
          icon: ArrowRight,
          to: `/missions/${mission.id}/control`,
          keywords: "console 3d simulate",
        });
      }
      return entries;
    });
    return [...pages, ...actions, ...missionCommands];
  }, [missions]);

  const visible = useMemo(() => {
    const filtered = query.trim() ? commands.filter((command) => matches(command, query)) : commands;
    return filtered.slice(0, 40);
  }, [commands, query]);

  useEffect(() => setActive(0), [query]);

  useEffect(() => {
    list.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const run = (command: Command | undefined) => {
    if (!command) return;
    onClose();
    navigate(command.to);
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((index) => Math.min(index + 1, visible.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((index) => Math.max(index - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      run(visible[active]);
    }
  };

  let lastGroup: string | null = null;

  return (
    <Modal open={open} onClose={onClose} title="Search and commands" placement="top" width="max-w-xl" bare>
      <div className="flex items-center gap-3 border-b border-line px-4">
        <Search className="h-4 w-4 shrink-0 text-subtle" strokeWidth={1.5} aria-hidden="true" />
        <input
          data-autofocus
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Search pages, missions and actions"
          aria-label="Search pages, missions and actions"
          aria-controls="command-list"
          aria-activedescendant={visible[active] ? `command-${visible[active].id}` : undefined}
          className="h-12 min-w-0 flex-1 bg-transparent text-sm text-fg outline-none placeholder:text-subtle focus-visible:ring-0 focus-visible:ring-offset-0"
        />
        <kbd className="kbd">esc</kbd>
      </div>

      <div ref={list} id="command-list" role="listbox" className="max-h-96 overflow-y-auto p-2">
        {visible.length === 0 && <p className="px-3 py-8 text-center text-sm text-muted">Nothing matches “{query}”.</p>}
        {visible.map((command, index) => {
          const Icon = command.icon;
          const heading = command.group !== lastGroup ? command.group : null;
          lastGroup = command.group;
          const selected = index === active;
          return (
            <div key={command.id}>
              {heading && (
                <div className="px-3 pb-1 pt-3 font-mono text-2xs uppercase tracking-wider text-subtle first:pt-1">{heading}</div>
              )}
              <div
                id={`command-${command.id}`}
                role="option"
                aria-selected={selected}
                data-index={index}
                onMouseMove={() => setActive(index)}
                onClick={() => run(command)}
                className={`flex h-10 cursor-pointer items-center gap-3 rounded-md px-3 text-sm ${
                  selected ? "bg-surface text-fg" : "text-muted"
                }`}
              >
                <Icon className={`h-4 w-4 shrink-0 ${selected ? "text-accent" : "text-subtle"}`} strokeWidth={1.5} />
                <span className="truncate">{command.label}</span>
                {command.hint && <span className="hidden truncate text-xs text-subtle sm:inline">{command.hint}</span>}
                {selected && <CornerDownLeft className="ml-auto h-3.5 w-3.5 shrink-0 text-subtle" strokeWidth={1.5} aria-hidden="true" />}
              </div>
            </div>
          );
        })}
      </div>

      <div className="flex items-center gap-4 border-t border-line px-4 py-2 text-2xs text-subtle">
        <span className="flex items-center gap-1">
          <kbd className="kbd">↑</kbd>
          <kbd className="kbd">↓</kbd> move
        </span>
        <span className="flex items-center gap-1">
          <kbd className="kbd">↵</kbd> open
        </span>
        <span className="ml-auto">{visible.length} results</span>
      </div>
    </Modal>
  );
}
