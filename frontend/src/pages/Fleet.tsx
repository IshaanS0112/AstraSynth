import { Check, Copy, Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import type { RoverConfig, RoverConfigInput } from "../api/types";
import { metres } from "../lib/format";
import { useAsync } from "../lib/useAsync";
import Modal from "../ui/Modal";
import PageHeader, { Section } from "../ui/PageHeader";
import { EmptyState, ErrorState } from "../ui/States";
import { useToast } from "../ui/Toast";

/** Metres on flat ground before the battery is empty: no slope, no reserve. */
function flatRange(rover: RoverConfig): number {
  return rover.battery_capacity_kwh / rover.energy_per_meter_kwh;
}

const USES: { name: string; use: string }[] = [
  {
    name: "Battery",
    use: "The traverse's energy budget. A plan using more than the configured margin (85% by default) is feasible with margin; more than all of it, infeasible.",
  },
  { name: "Slope limit", use: "A hard constraint: every move steeper than this is removed from the search graph." },
  {
    name: "Energy draw",
    use: "Energy = draw × distance × (1 + k·|rise/run|), so a climb costs more than the same distance on the flat.",
  },
  { name: "Footprint", use: "Inscribed chassis radius. Ground that close to a lethal cell is lethal too." },
  { name: "Clearance", use: "Keep-out band. Cost decays across it, so plans prefer to stay this far from obstacles." },
];

export default function Fleet() {
  const [params, setParams] = useSearchParams();
  const { data, error, loading, reload } = useAsync(() => api.listRoverConfigs(), []);
  const creating = params.get("new") === "1";
  const setCreating = (open: boolean) => setParams(open ? { new: "1" } : {}, { replace: true });

  const longest = Math.max(1, ...(data ?? []).map(flatRange));

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 lg:px-8 lg:py-8">
      <PageHeader
        title="Fleet"
        description="Rover configurations the planners plan for. Planning parameters, illustrative rather than manufacturer specifications."
        actions={
          <button className="btn-primary" onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" strokeWidth={1.5} aria-hidden="true" />
            Add configuration
          </button>
        }
      />

      <section className="panel mb-8 grid overflow-hidden lg:grid-cols-12">
        <figure className="dot-grid relative flex items-center justify-center border-b border-line p-6 pb-14 lg:col-span-6 lg:border-b-0 lg:border-r">
          <img
            src="/3d/rover-studio.webp"
            alt="The AstraSynth rover: six wheels on a rocker-bogie suspension, a mast camera and a high-gain antenna"
            className="h-56 w-auto max-w-full object-contain sm:h-72"
          />
          <figcaption className="absolute bottom-3 left-3 rounded border border-line bg-canvas/80 px-2 py-1 font-mono text-2xs uppercase tracking-wider text-muted">
            Blender · the model mission control drives
          </figcaption>
        </figure>
        <div className="p-6 lg:col-span-6">
          <div className="label-tick mb-3">How the planners use each number</div>
          <dl className="divide-y divide-line">
            {USES.map((entry) => (
              <div key={entry.name} className="grid gap-1 py-3 sm:grid-cols-[7rem_1fr] sm:gap-4">
                <dt className="text-sm font-medium text-fg">{entry.name}</dt>
                <dd className="text-sm leading-6 text-muted">{entry.use}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      <Section
        label="Configurations"
        title={
          data && data.length
            ? `${data.length} configurations · ${metres(Math.min(...data.map(flatRange)))} to ${metres(longest)} on a full battery`
            : undefined
        }
      >
        {error && !data ? (
          <ErrorState error={error} onRetry={reload} title="Could not load the fleet" />
        ) : loading && !data ? (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <div key={i} className="panel space-y-3 p-5">
                <div className="skeleton h-5 w-1/2" />
                <div className="skeleton h-8 w-1/3" />
                <div className="skeleton h-24 w-full" />
              </div>
            ))}
          </div>
        ) : data && data.length === 0 ? (
          <EmptyState
            title="No rover configurations"
            body="The API seeds three on startup. Add one to plan for your own chassis, battery and slope limit."
            action={
              <button className="btn-primary" onClick={() => setCreating(true)}>
                Add configuration
              </button>
            }
          />
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {data?.map((rover) => <RoverCard key={rover.id} rover={rover} longest={longest} />)}
          </div>
        )}
      </Section>

      <RoverForm
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={() => {
          setCreating(false);
          reload();
        }}
      />
    </div>
  );
}

function RoverCard({ rover, longest }: { rover: RoverConfig; longest: number }) {
  const notify = useToast();
  const [copied, setCopied] = useState(false);
  const range = flatRange(rover);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(rover.id);
      setCopied(true);
      notify({ title: "Configuration ID copied", body: "Pass it as rover_config_id to the API.", tone: "success" });
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      notify({ title: "Could not copy", body: rover.id, tone: "danger" });
    }
  };

  const specs: [string, string][] = [
    ["Battery", `${rover.battery_capacity_kwh} kWh`],
    ["Slope limit", `${rover.max_traversable_slope_deg}°`],
    ["Energy draw", `${(rover.energy_per_meter_kwh * 1000).toFixed(1)} Wh/m`],
    ["Footprint", rover.footprint_radius_m ? `${rover.footprint_radius_m} m` : "Point"],
    ["Clearance", rover.clearance_radius_m ? `${rover.clearance_radius_m} m` : "None"],
  ];

  return (
    <article className="panel flex flex-col gap-4 p-5">
      <div className="flex items-start justify-between gap-3">
        <h3 className="text-base font-semibold text-fg">{rover.name}</h3>
        <button
          onClick={copy}
          className="-m-1 flex h-8 shrink-0 items-center gap-1.5 rounded-md px-2 font-mono text-2xs uppercase tracking-wider text-subtle transition-colors duration-120 hover:bg-surface-2 hover:text-fg"
          aria-label={`Copy the ID of ${rover.name}`}
        >
          {copied ? <Check className="h-3.5 w-3.5 text-success" strokeWidth={1.5} /> : <Copy className="h-3.5 w-3.5" strokeWidth={1.5} />}
          {rover.id.slice(0, 8)}
        </button>
      </div>

      <div>
        <div className="flex items-baseline justify-between">
          <span className="label-tick">Flat-ground range</span>
          <span className="num text-2xl font-medium text-fg">{metres(range)}</span>
        </div>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-line-strong">
          <div className="h-full rounded-full bg-muted" style={{ width: `${(range / longest) * 100}%` }} />
        </div>
        <p className="mt-1.5 text-2xs text-subtle">Battery ÷ draw. Slopes and detours shorten it.</p>
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 border-t border-line pt-4 text-sm">
        {specs.map(([label, value]) => (
          <div key={label} className="flex items-center justify-between gap-2">
            <dt className="text-muted">{label}</dt>
            <dd className="num text-fg">{value}</dd>
          </div>
        ))}
      </dl>
    </article>
  );
}

const EMPTY = {
  name: "",
  battery: "6",
  slope: "25",
  draw: "3.0",
  footprint: "1.2",
  clearance: "12",
};

/** The API's own rules, checked before the round trip so the message lands on the field. */
function validate(form: typeof EMPTY): Partial<Record<keyof typeof EMPTY, string>> {
  const problems: Partial<Record<keyof typeof EMPTY, string>> = {};
  const n = (value: string) => Number(value);
  if (!form.name.trim()) problems.name = "Give it a name.";
  if (!(n(form.battery) > 0)) problems.battery = "More than 0 kWh.";
  if (!(n(form.slope) > 0 && n(form.slope) <= 89)) problems.slope = "Between 0 and 89°.";
  if (!(n(form.draw) > 0)) problems.draw = "More than 0 Wh/m.";
  if (!(n(form.footprint) >= 0 && n(form.footprint) <= 50)) problems.footprint = "0 to 50 m.";
  if (!(n(form.clearance) >= 0 && n(form.clearance) <= 500)) problems.clearance = "0 to 500 m.";
  else if (n(form.clearance) > 0 && n(form.clearance) < n(form.footprint))
    problems.clearance = "At least the footprint, or 0 for none.";
  return problems;
}

function RoverForm({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (rover: RoverConfig) => void }) {
  const notify = useToast();
  const [form, setForm] = useState(EMPTY);
  const [touched, setTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setForm(EMPTY);
      setTouched(false);
      setServerError(null);
    }
  }, [open]);

  const problems = validate(form);
  const shown = touched ? problems : {};

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setTouched(true);
    if (Object.keys(problems).length) return;
    setSaving(true);
    setServerError(null);
    const body: RoverConfigInput = {
      name: form.name.trim(),
      battery_capacity_kwh: Number(form.battery),
      max_traversable_slope_deg: Number(form.slope),
      energy_per_meter_kwh: Number(form.draw) / 1000,
      footprint_radius_m: Number(form.footprint),
      clearance_radius_m: Number(form.clearance),
    };
    try {
      const rover = await api.createRoverConfig(body);
      notify({ title: `${rover.name} added`, body: "It is in every rover picker now.", tone: "success" });
      onCreated(rover);
    } catch (exc) {
      setServerError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  };

  const field = (key: keyof typeof EMPTY, label: string, unit: string | null, hint?: string) => (
    <div>
      <label className="label" htmlFor={`rover-${key}`}>
        {label}
      </label>
      <div className="relative">
        <input
          id={`rover-${key}`}
          className={`field ${unit ? "pr-14" : ""} ${shown[key] ? "border-danger" : ""}`}
          inputMode={key === "name" ? "text" : "decimal"}
          value={form[key]}
          onChange={(event) => setForm((current) => ({ ...current, [key]: event.target.value }))}
          aria-invalid={Boolean(shown[key])}
          aria-describedby={`rover-${key}-note`}
          data-autofocus={key === "name" ? true : undefined}
          placeholder={key === "name" ? "Survey-Class (long range)" : undefined}
        />
        {unit && (
          <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 font-mono text-2xs text-subtle">{unit}</span>
        )}
      </div>
      <p id={`rover-${key}-note`} className={`mt-1 text-2xs ${shown[key] ? "text-danger" : "text-subtle"}`}>
        {shown[key] ?? hint ?? " "}
      </p>
    </div>
  );

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Add rover configuration"
      description="Planning parameters. Every planner and study can use it as soon as it is saved."
      footer={
        <>
          <button type="button" className="btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="rover-form" className="btn-primary" disabled={saving}>
            {saving ? "Saving…" : "Save configuration"}
          </button>
        </>
      }
    >
      <form id="rover-form" onSubmit={submit} noValidate className="grid gap-x-4 gap-y-1 sm:grid-cols-2">
        <div className="sm:col-span-2">{field("name", "Name", null)}</div>
        {field("battery", "Battery", "kWh")}
        {field("slope", "Slope limit", "deg", "Steeper moves are removed.")}
        {field("draw", "Energy draw", "Wh/m", "On flat ground.")}
        {field("footprint", "Footprint radius", "m", "0 is a point rover.")}
        <div className="sm:col-span-2">{field("clearance", "Clearance radius", "m", "0 for no keep-out band.")}</div>
        {serverError && (
          <p role="alert" className="rounded-md border border-danger/30 bg-danger/5 px-3 py-2 text-xs text-danger sm:col-span-2">
            {serverError}
          </p>
        )}
      </form>
    </Modal>
  );
}
