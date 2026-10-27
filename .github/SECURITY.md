# Security

## Scope

AstraSynth is a planning simulation intended to run locally or behind a trusted
network boundary. It has **no authentication layer** - every endpoint is open to
anyone who can reach the port. Do not expose it to the public internet as-is.

Known deliberate limits, documented rather than hidden:

- No authentication or authorisation on any route.
- Uploads are extension-allowlisted and size-capped at 25 MB, but not
  content-sniffed. A renamed non-image fails at decode time with a 422.
- The API returns detailed error messages, including planner diagnostics. That
  is useful for a local operator and would be information disclosure in a
  multi-tenant deployment.

## Reporting

Open a GitHub issue for anything affecting a local deployment. For something you
believe should not be public, use GitHub's private vulnerability reporting on
the Security tab.

## Secrets

`ANTHROPIC_API_KEY` is read from the environment and never written to the
database, the logs, or any API response. `backend/.env` is gitignored;
`backend/.env.example` carries the key names only.
