# Public Site

This directory is a static, sanitized public demo site for the Agent Evaluation Framework.

## Contents

- `index.html`: public landing page.
- `project_logic/`: static architecture explanation pages.
- `webshow/`: static report visualization page.
- `webshow/report_data.js`: sanitized report data generated from local reports.

## Deployment

Use `public_site/` as the static site root.

For Vercel:

1. Push this directory to a GitHub repository.
2. Import the repository in Vercel.
3. Set the output/static directory to `public_site` if the repository contains other files.

For GitLab Pages:

1. Use `public_site/` as the artifact directory.
2. Serve it as static files through GitLab Pages or an internal static site service.

## Privacy Notes

The public site should not include:

- real browser capture logs;
- raw `events.jsonl` / normalized run files;
- API keys or environment files;
- internal system URLs;
- cookies, tokens, headers, or authentication metadata.

