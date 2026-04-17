Review and update the project documentation based on recent code changes.

Steps:
1. Run `git log --oneline -20` to see recent commit history
2. Run `git diff HEAD~5..HEAD --stat` to see which files changed
3. Read README.md, docs/architecture.md, docs/deployment.md in full
4. Read the changed source files that are relevant to docs
5. For each doc file, identify what is stale, missing, or wrong
6. Make targeted edits — update only sections that are actually out of date

Scope of each doc:
- README.md: project purpose, quick-start, environment variables, how to run locally
- docs/architecture.md: module structure, data flow, integration boundaries, key design decisions
- docs/deployment.md: Docker setup, environment config, migration steps, health check

Rules:
- Do not rewrite sections that are still accurate
- Do not add speculative content or TODOs
- Keep the Russian-language bot context in mind (e.g. don't anglicize UX descriptions)
- If a section needs a large rewrite, summarize the proposed change and ask before proceeding
