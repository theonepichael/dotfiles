# Secret handling

Priority-0 safety reminder for this repository.

## Where secrets belong

- `~/.secrets` (already ignored by `.gitignore`).
- Environment variables.
- A system vault or password manager.

Secrets must **never** be committed inline in scripts, docs, commit messages,
backlog items, plan text, or any other file that can end up in git history.

## Before every commit

1. Stage only the files you intended to change.
2. Review the staged diff:

   ```bash
   git diff --staged
   ```

3. Look for API keys, passwords, tokens, personal identifiers, or anything
   that should live in `~/.secrets` instead.
4. Apply the Shell Command Safety discipline from `CLAUDE.md` when quoting
   freeform text in shell commands.

## If a secret is accidentally committed

1. **Rotate the secret immediately.** Assume it is compromised.
2. Treat history rewrite (e.g., force-pushing a cleaned history) as a separate,
   deliberate follow-up operation; do not attempt it without a plan.
