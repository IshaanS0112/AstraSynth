import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api/client";

export default function NewMission() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [source, setSource] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
      navigate(`/missions/${mission.id}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      setSubmitting(false);
    }
  };

  return (
    <div className="mx-auto max-w-2xl px-6 py-6">
      <Link to="/" className="text-xs text-slate-500 hover:text-accent">
        ← all missions
      </Link>
      <h1 className="mb-4 mt-1 text-xl font-semibold text-slate-100">New mission</h1>

      <form onSubmit={submit} className="panel space-y-4 p-5">
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
          <p className="mt-1 text-[11px] text-slate-500">
            Recorded with the mission and shown on every report, so a result can always be
            traced back to the data it came from.
          </p>
        </div>

        <div>
          <label className="label" htmlFor="image">
            Terrain image (grayscale DEM · png, jpg, tif)
          </label>
          <input
            id="image"
            type="file"
            accept=".png,.jpg,.jpeg,.tif,.tiff"
            required
            className="field file:mr-3 file:rounded file:border-0 file:bg-edge file:px-3 file:py-1 file:text-xs file:text-slate-200"
            onChange={(event) => handleFile(event.target.files?.[0] ?? null)}
          />
        </div>

        {preview && (
          <img
            src={preview}
            alt="Selected terrain"
            className="max-h-64 w-full rounded border border-edge object-contain"
          />
        )}

        {error && <p className="text-sm text-rose-400">{error}</p>}

        <button className="btn-primary w-full" disabled={!file || !name || submitting}>
          {submitting ? "Uploading…" : "Create mission"}
        </button>
      </form>
    </div>
  );
}
