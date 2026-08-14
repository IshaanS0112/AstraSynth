## What this changes

<!-- One or two sentences. -->

## Why

<!-- The problem this solves. Link an issue if there is one. -->

## Checklist

- [ ] `cd backend && python -m pytest` passes
- [ ] `ruff check .` and `ruff format --check .` pass
- [ ] `cd frontend && npm run typecheck && npm run build` pass

## Does this change any documented number?

<!--
The README and docs/architecture.md quote concrete measured values - hazard
scores, path distances, node-expansion counts, classification thresholds. If a
change to the CV pipeline, the cost function or the risk formula moves any of
them, update the docs in the same PR. Stale numbers are worse than none.
-->

- [ ] No documented figures change
- [ ] Documented figures change, and I have updated the README / architecture.md
