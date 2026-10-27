import { ImageUp } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api/client";
import { rememberMissionName } from "../lib/missions";
import PageHeader from "../ui/PageHeader";
import { useToast } from "../ui/Toast";

const ACCEPT = ".png,.jpg,.jpeg,.tif,.tiff";

export default function NewMission() {
  const navigate = useNavigate();
  const notify = useToast();
  const [name, setName] = useState("");
  const [source, setSource] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Object URLs hold the file in memory until revoked.
  useEffect(() => {
    return () => {
      if (preview) URL.revokeObjectURL(preview);
    };
  }, [preview]);

  const handleFile = (selected: File | null) => {
    setFile(selected);
    setPreview(selected ? URL.createObjectURL(selected) : null);
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!file) return;
    setSubmitting(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("name", name);
      if (source) form.append("terrain_source", source);
      form.append("terrain_image", file);
      const mission = await api.createMission(form);
      rememberMissionName(mission);
      notify({ title: "Mission created", body: "Run terrain analysis to open it in mission control.", tone: "success" });
      navigate(`/missions/${mission.id}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      setSubmitting(false);
    }
  };

  return (
    <div className="mx-auto max-w-2xl px-4 py-6 lg:px-8 lg:py-8">
      <PageHeader
        title="New mission"
        description="One terrain tile per mission. AstraSynth derives slope, hazard and obstacles from it, then plans and simulates over the result."
      />

      <form onSubmit={submit} className="panel space-y-5 p-5 sm:p-6">
        <div>
          <label className="label" htmlFor="name">
            Mission name
          </label>
          <input
            id="name"
            className="field"
            required
            maxLength={200}
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Jezero north rim traverse"
          />
        </div>

        <div>
          <label className="label" htmlFor="source">
            Terrain source
          </label>
          <input
            id="source"
            className="field"
            maxLength={100}
            value={source}
            onChange={(event) => setSource(event.target.value)}
            placeholder="USGS MOLA/HRSC 200m v2, or synthetic"
          />
          <p className="mt-1 text-xs text-muted">
            Recorded with the mission and shown on every report, so a result can always be traced back to the data it came from.
          </p>
        </div>

        <div>
          <span className="label">Terrain image</span>
          <label
            htmlFor="image"
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              const dropped = event.dataTransfer.files?.[0];
              if (dropped) handleFile(dropped);
            }}
            className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed px-4 py-8 text-center transition-colors duration-120 ${
              dragging ? "border-accent bg-accent/5" : "border-line-strong bg-surface-2 hover:border-muted"
            }`}
          >
            {preview ? (
              <img src={preview} alt="Selected terrain" className="max-h-56 w-full rounded-md object-contain" />
            ) : (
              <ImageUp className="h-6 w-6 text-subtle" strokeWidth={1.5} aria-hidden="true" />
            )}
            <span className="text-sm text-fg">{file ? file.name : "Drop a grayscale DEM here, or choose a file"}</span>
            <span className="text-xs text-muted">
              {file ? `${(file.size / 1024).toFixed(0)} KB · choose another to replace it` : "PNG, JPEG or TIFF. Sample tiles are in data/sample_terrain/."}
            </span>
          </label>
          {/* Not `required`: a dropped file never reaches this input, and the
              browser would block the submit over it. The button checks instead. */}
          <input
            id="image"
            type="file"
            accept={ACCEPT}
            className="sr-only"
            onChange={(event) => handleFile(event.target.files?.[0] ?? null)}
          />
        </div>

        {error && (
          <p role="alert" className="rounded-md border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger">
            {error}
          </p>
        )}

        <div className="flex justify-end gap-2 border-t border-line pt-5">
          <button type="button" className="btn-ghost" onClick={() => navigate(-1)}>
            Cancel
          </button>
          <button className="btn-primary" disabled={!file || !name || submitting}>
            {submitting ? "Uploading…" : "Create mission"}
          </button>
        </div>
      </form>
    </div>
  );
}
