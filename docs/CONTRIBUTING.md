# Contributing — NIF Office Management System

## Branching & review
- `main` is protected and always deployable. Work on `feat/…`, `fix/…`, `chore/…` branches.
- Open a **Pull Request** into `main`; **require ≥1 review** and **all CI checks green** before merge.
  - Enable in GitHub → Settings → Branches → Branch protection for `main`:
    *Require a pull request*, *Require status checks* (`Backend`, `Frontend`, `Commit messages`, `Secret scan`),
    *Require branches up to date*, *Require linear history*.

## Commit messages — Conventional Commits
Format: `type(scope): summary` (≤100 chars). Enforced in CI (commitlint) and, optionally, locally.

- **types:** `feat`, `fix`, `perf`, `refactor`, `security`, `test`, `docs`, `build`, `ci`, `chore`, `revert`
- **examples:**
  - `fix(leaves): dept-head approval grants leave and deducts balance atomically`
  - `security(media): serve uploads via signed URLs; remove public /media route`
  - `feat(inventory): asset assignment + take-out approval workflow`
- Body: what & why. Footer: `BREAKING CHANGE: …`, `Refs #123`.

### Enforce locally (optional)
```bash
npm i -D @commitlint/cli @commitlint/config-conventional husky   # at repo root or frontend/
npx husky init
echo 'npx --no -- commitlint --edit "$1"' > .husky/commit-msg
```
Or set a template: `git config commit.template .gitmessage`.

## CI gates (must pass to merge)
- **Backend:** `ruff check`, `makemigrations --check` (no drift), `pytest` (+coverage, incl. the media-security test), `pip-audit`.
- **Frontend:** `eslint`, `vitest`, `vite build`, `npm audit`.
- **Repo:** commitlint (PRs), gitleaks secret scan.

Run locally before pushing:
```bash
# backend (in the container or a venv with requirements/development.txt)
cd backend && ruff check . && python manage.py makemigrations --check --dry-run && pytest -q
# frontend
cd frontend && npm run lint && npm test && npm run build
```

## Keep the CHANGELOG
Add a line under `## [Unreleased]` in `CHANGELOG.md` for every user-facing change
(Added / Changed / Fixed / Security), grouped by area.
