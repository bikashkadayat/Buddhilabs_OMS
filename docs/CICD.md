# CI/CD — GitHub Actions → self-hosted VPS

Two workflows:

| File | Trigger | Purpose |
|---|---|---|
| `.github/workflows/ci.yml` | push + PR to `main` / `develop` | Test, lint, build, migration-drift, secret scan. Blocks merge. |
| `.github/workflows/deploy.yml` | after **CI succeeds** on `main` | SSH to the VPS, rebuild, migrate, health-check, roll back on failure. |

`ci.yml` already existed and needed no changes. `deploy.yml` is the new piece.

---

## 1. Why `workflow_run` (and not `needs:`)

`needs:` only orders jobs *inside one workflow*. Since deploy is a separate file, the
reliable chain is `workflow_run`, guarded so a red or non-`main` build can never deploy:

```yaml
on:
  workflow_run:
    workflows: ["CI"]      # matches `name: CI` in ci.yml — keep them in sync
    types: [completed]
    branches: [main]
```
```yaml
if: github.event.workflow_run.conclusion == 'success' &&
    github.event.workflow_run.event == 'push'
```

* `conclusion == 'success'` — a failed/cancelled CI never deploys.
* `event == 'push'` — a merged PR (or direct push) deploys; a PR *build* never does.
* The deploy pins the **exact commit CI verified** (`workflow_run.head_sha`), not
  whatever `main` happens to point at when the job starts.

> `workflow_run` always executes the copy of `deploy.yml` on the **default branch**.
> Edits to it only take effect once merged to `main`.

---

## 2. Required GitHub Secrets

Settings → Secrets and variables → Actions → **New repository secret**.

| Secret | Required | Example | Notes |
|---|---|---|---|
| `VPS_HOST` | ✅ | `203.0.113.10` or `nif.example.com` | IP or DNS of the VPS. |
| `VPS_USER` | ✅ | `deploy` | SSH user; must be in the `docker` group. |
| `VPS_SSH_KEY` | ✅ | *(private key, full PEM)* | **Private** half of the deploy keypair. Paste the whole file, including the BEGIN/END lines. |
| `VPS_PROJECT_PATH` | ✅ | `/srv/nif` | Directory on the VPS holding the git checkout + `.env`. |
| `VPS_SSH_PORT` | ⬜ | `2222` | Only if SSH is not on 22. Defaults to 22. |
| `HEALTHCHECK_URL` | ⬜ | `https://nif.example.com/api/v1/health/` | Adds a public end-to-end probe (DNS + host nginx + TLS + Django). Skipped if unset. |

**Everything else stays on the VPS.** `DJANGO_SECRET_KEY`, `DATABASE_PASSWORD`,
SMTP, S3 and backup keys are read from the gitignored `.env` in
`VPS_PROJECT_PATH` by `docker-compose.prod.yml`. Do **not** duplicate them into
GitHub — the deploy never needs to see them.

---

## 3. SSH keys — you need TWO, in opposite directions

This trips people up. They are different keys with different jobs.

### Key A — GitHub Actions ➜ VPS (so the workflow can log in)

On your **workstation** (never on the VPS — the private half must not live there):

```bash
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/nif_deploy -N ""
# -> ~/.ssh/nif_deploy      (PRIVATE — goes into the GitHub secret)
# -> ~/.ssh/nif_deploy.pub  (PUBLIC  — goes onto the VPS)
```

Install the **public** half on the VPS:

```bash
ssh-copy-id -i ~/.ssh/nif_deploy.pub deploy@YOUR_VPS
# or manually:
#   cat ~/.ssh/nif_deploy.pub | ssh deploy@YOUR_VPS 'mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && chmod 700 ~/.ssh'
```

Put the **private** half into `VPS_SSH_KEY`:

```bash
cat ~/.ssh/nif_deploy      # copy ALL of it, including the BEGIN/END lines
```

Verify it works before trusting CI:

```bash
ssh -i ~/.ssh/nif_deploy deploy@YOUR_VPS 'cd /srv/nif && docker compose -f docker-compose.prod.yml ps'
```

Optional hardening — restrict the key in the VPS `~/.ssh/authorized_keys`:

```
from="140.82.112.0/20,143.55.64.0/20",no-agent-forwarding,no-port-forwarding,no-X11-forwarding ssh-ed25519 AAAA... github-actions-deploy
```
(GitHub's egress ranges change; `from=` is optional and needs maintenance.)

### Key B — VPS ➜ GitHub (so `git fetch` works on a **private** repo)

The deploy runs `git fetch` **on the VPS**, so the VPS needs read access to the repo.

```bash
# ON THE VPS, as the deploy user:
ssh-keygen -t ed25519 -C "nif-vps-readonly" -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub
```

Add that public key to **the repo** → Settings → Deploy keys → *Add deploy key*
(**read-only** — do not tick "Allow write access"). Then confirm:

```bash
ssh -T git@github.com          # expect: "you've successfully authenticated"
cd /srv/nif && git fetch origin main && echo OK
```

If the repo is public over HTTPS, Key B is unnecessary.

---

## 4. VPS prerequisites (one time)

```bash
# The deploy user must run docker without sudo:
sudo usermod -aG docker deploy && newgrp docker

cd /srv/nif                       # == VPS_PROJECT_PATH
git remote -v                     # must point at this repo
test -f .env && echo ".env present"   # required; see .env.example
docker compose -f docker-compose.prod.yml ps
```

The checkout must be a real clone on branch `main`; the deploy does
`git checkout -B main && git reset --hard <sha>`.

> **`git reset --hard` only rewrites tracked files.** Your `.env` is gitignored and
> the data lives in named volumes (`postgres_data`, `media_data`, `backups_data`), so
> neither is touched. Do not keep hand-edited **tracked** files on the VPS — they
> will be overwritten.

---

## 5. What the deploy actually does

```
git fetch origin main
git reset --hard <the exact SHA CI verified>
docker compose -f docker-compose.prod.yml up -d --build
   └─ backend image CMD runs: migrate --noinput
                              collectstatic --noinput --clear
                              gunicorn
wait for nif-backend -> healthy      (compose healthcheck polls /api/v1/health/)
docker compose exec backend python manage.py migrate --noinput   # explicit gate (no-op)
docker compose exec backend  ... urlopen /api/v1/health/          # direct hit on gunicorn
curl https://<domain>/api/v1/health/                              # optional public probe
```

### Migrations and collectstatic are **already** in the image CMD

`Dockerfile.backend` runs `migrate` then `collectstatic --clear` *before* Gunicorn
binds. So the deploy does not re-run `collectstatic`: doing it on a live container
would briefly delete the static files Gunicorn is serving (`--clear` wipes first).
Letting it run at boot keeps that window closed. The `migrate` step is repeated
only as an explicit, idempotent gate — it normally prints *"No migrations to apply"*.

### Rollback

If the backend never reports healthy, the script resets to the previous commit,
rebuilds, and fails the job.

> **Caveat, read this:** rollback reverts **code only** — applied migrations are
> **not** reversed. It is safe for a bad build or a runtime error; a release with a
> destructive/backward-incompatible migration needs a manual DB restore
> (`docs/BACKUP.md`). Keep migrations backward-compatible for one release if you
> want rollback to stay a one-click operation.

### Downtime

`up -d --build` recreates the backend container: **a few seconds of downtime** while
it migrates, collects static and boots Gunicorn. This is *minimal*-downtime, not
zero. True zero-downtime needs multiple backend replicas behind the proxy with a rolling
restart — impossible today because `docker-compose.prod.yml` pins
`container_name: nif-backend` (a fixed name forbids >1 replica). Worth it only if a
few seconds actually hurts.

---

## 6. Branch protection

Settings → Branches → Add rule for `main`:

* ✅ Require a pull request before merging (≥1 approval)
* ✅ Require status checks to pass — select **exactly** these:
  * `Backend (pytest + ruff + migrations + audit)`
  * `Frontend (vitest + eslint + build + audit)`
  * `Secret scan (gitleaks)`
* ✅ Require branches to be up to date before merging
* ✅ Require linear history

> **The names must match the job `name:` in `ci.yml` character for character.**
> A required check that never reports blocks every PR forever. `docs/CONTRIBUTING.md`
> currently lists `Backend`, `Frontend`, `Commit messages`, `Secret scan` — those are
> **wrong**: the first two are truncated, and **there is no commitlint job in
> `ci.yml`**, so `Commit messages` would never arrive. Use the list above.

Do **not** mark `Deploy to VPS` as a required check — it runs *after* the merge.

---

## 7. First run

1. Add the secrets (§2) and both keys (§3).
2. Merge any commit to `main`.
3. Actions → **CI** goes green → **Deploy** starts automatically.
4. Watch the `Deploy over SSH` step; it prints the target SHA and ends with
   `==> deployed <sha> successfully` plus `docker compose ps`.
5. Confirm: `curl -i https://<your-domain>/api/v1/health/` → `200 {"status":"ok"}`.

Manual deploy / rollback: Actions → Deploy → **Run workflow** → optionally paste a
SHA to pin an older release.
