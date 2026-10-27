/** Just enough of the Prometheus text format to read this API's own metrics. */

export interface Sample {
  name: string;
  labels: Record<string, string>;
  value: number;
}

const LINE = /^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?\s+(\S+)/;
const LABEL = /(\w+)="((?:[^"\\]|\\.)*)"/g;

export function parsePrometheus(text: string): Sample[] {
  const samples: Sample[] = [];
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const match = LINE.exec(line);
    if (!match) continue;
    const labels: Record<string, string> = {};
    for (const [, key, value] of (match[2] ?? "").matchAll(LABEL)) labels[key] = value.replace(/\\"/g, '"');
    samples.push({ name: match[1], labels, value: Number(match[3]) });
  }
  return samples;
}

/** A latency estimate. ``atLeast`` means it fell past the last finite bucket. */
export interface Quantile {
  seconds: number;
  atLeast: boolean;
}

/**
 * The q-quantile of a cumulative histogram, interpolated linearly inside the
 * bucket it falls in: the same estimate as PromQL's histogram_quantile.
 */
export function histogramQuantile(q: number, buckets: { le: number; count: number }[]): Quantile | null {
  const sorted = [...buckets].sort((a, b) => a.le - b.le);
  const total = sorted[sorted.length - 1]?.count ?? 0;
  if (total === 0) return null;
  const rank = q * total;
  let previousLe = 0;
  let previousCount = 0;
  for (const bucket of sorted) {
    if (bucket.count >= rank) {
      // The open-ended bucket has no upper bound to interpolate to: all that
      // is known is that the value is beyond the last finite one.
      if (!Number.isFinite(bucket.le)) return { seconds: previousLe, atLeast: true };
      const inBucket = bucket.count - previousCount;
      const fraction = inBucket > 0 ? (rank - previousCount) / inBucket : 0;
      return { seconds: previousLe + (bucket.le - previousLe) * fraction, atLeast: false };
    }
    previousLe = bucket.le;
    previousCount = bucket.count;
  }
  return { seconds: previousLe, atLeast: true };
}

export interface RouteStats {
  route: string;
  methods: string[];
  requests: number;
  clientErrors: number;
  serverErrors: number;
  meanSeconds: number | null;
  p95: Quantile | null;
}

/** Requests, errors and latency per route template, plus the same over every route. */
export function summarise(samples: Sample[]) {
  const routes = new Map<string, RouteStats>();
  const route = (name: string) => {
    if (!routes.has(name)) {
      routes.set(name, { route: name, methods: [], requests: 0, clientErrors: 0, serverErrors: 0, meanSeconds: null, p95: null });
    }
    return routes.get(name)!;
  };

  const buckets = new Map<string, { le: number; count: number }[]>();
  const sums = new Map<string, number>();
  const counts = new Map<string, number>();
  const overall = new Map<number, number>();

  for (const sample of samples) {
    const name = sample.labels.route;
    if (sample.name === "astra_http_requests_total" && name) {
      const stats = route(name);
      stats.requests += sample.value;
      const status = Number(sample.labels.status);
      if (status >= 500) stats.serverErrors += sample.value;
      else if (status >= 400) stats.clientErrors += sample.value;
      if (sample.labels.method && !stats.methods.includes(sample.labels.method)) stats.methods.push(sample.labels.method);
    } else if (sample.name === "astra_http_request_seconds_bucket" && name) {
      const le = sample.labels.le === "+Inf" ? Infinity : Number(sample.labels.le);
      if (!buckets.has(name)) buckets.set(name, []);
      buckets.get(name)!.push({ le, count: sample.value });
      overall.set(le, (overall.get(le) ?? 0) + sample.value);
    } else if (sample.name === "astra_http_request_seconds_sum" && name) {
      sums.set(name, sample.value);
    } else if (sample.name === "astra_http_request_seconds_count" && name) {
      counts.set(name, sample.value);
    }
  }

  for (const [name, list] of buckets) {
    const stats = route(name);
    const n = counts.get(name) ?? 0;
    stats.meanSeconds = n > 0 ? (sums.get(name) ?? 0) / n : null;
    stats.p95 = histogramQuantile(0.95, list);
  }

  const all = [...overall.entries()].map(([le, count]) => ({ le, count }));
  const list = [...routes.values()].sort((a, b) => b.requests - a.requests);
  return {
    routes: list,
    requests: list.reduce((acc, r) => acc + r.requests, 0),
    serverErrors: list.reduce((acc, r) => acc + r.serverErrors, 0),
    clientErrors: list.reduce((acc, r) => acc + r.clientErrors, 0),
    p50: histogramQuantile(0.5, all),
    p95: histogramQuantile(0.95, all),
    queueDepth: samples
      .filter((sample) => sample.name === "astra_jobs_queue_depth")
      .map((sample) => ({ status: sample.labels.status, value: sample.value })),
  };
}
