---
name: ship
description: Stage all changes, generate a meaningful commit message from the diff, commit, and push to origin
---

1. Run `git status` and `git diff HEAD` to understand what changed.
2. Stage all relevant modified and new files. Skip any secrets or `.env` files.
3. Write a concise, accurate commit message based on the actual diff — use the imperative mood, keep the subject under 72 characters. Add a body if the change is non-trivial.
4. Commit using a HEREDOC with the message and this trailer:
   Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
5. Push to origin on the current branch.
6. Report the commit hash and a one-line summary of what was pushed.
