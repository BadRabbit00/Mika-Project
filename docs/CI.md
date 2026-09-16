# Continuous integration and repository privacy

`.github/workflows/ci.yml` runs for pull requests targeting main or develop,
pushes to either branch, merge queues, and manual dispatch. There are no path
filters: documentation changes also receive the required checks.

## Checks

- **Nix tests** runs `nix flake check`: the complete pytest suite, lock validation,
  Ruff lint and formatting, Nix formatting, and the offline CLI startup. It also
  builds the production environment from the locked uv2nix dependency graph.
- **Repository audit** rejects private files in the Git index, validates the
  workflow with actionlint, and scans all fetched Git history with Gitleaks.
  The Telegram rule also detects credentials without a variable name or URL.

Actions are pinned to commit hashes. Jobs have read-only repository permissions
and never receive deployment credentials. Tests use synthetic articles and
mocked external services. No Telegram messages, model requests, or deployments
are performed by CI.

## Required checks on main

Protect main with pull requests, the **Nix tests** and **Repository audit** status
checks, and the requirement to be up to date before merging. Apply the policy to
administrators as well and disallow force pushes and branch deletion. These are
GitHub repository settings; a workflow file alone cannot enforce merge blocking.
The owner needs authenticated repository administration access to enable them.

## Private files and public templates

The following paths stay on the operator's machine:

- `library/`, including its topic catalogue and articles;
- `config/telegram.yaml` and `config/telegram.yml`;
- `config/world-*.json`;
- `.env`, `.env.*`, and `*.env`, except the empty `.env.example` template.

The exclusions for the library and deployment configuration are rooted at the
repository root. Files with the same names under `mika-startup/startup/config/`
are public templates and remain versioned. The index check rejects forbidden
files even when someone uses `git add --force`.

Removing a file from the index preserves its local copy. The previously tracked
library catalogue was removed from the current tree; earlier commits retain
that catalogue metadata. No article bodies or filled Telegram configuration
were found in Git history. The credential audit did not find a secret requiring
a history rewrite.

## Local checks

```sh
nix flake check --no-update-lock-file --print-build-logs
nix build .#default --no-link --no-update-lock-file
nix develop .#audit --command python scripts/check_repository.py
nix develop .#audit --command actionlint
nix develop .#audit --command gitleaks git \
  --config config/gitleaks.toml --log-opts="--all --full-history -m" \
  --redact=100 --no-banner
nix develop .#audit --command gitleaks git --staged \
  --config config/gitleaks.toml --redact=100 --no-banner
```

The history scan covers committed revisions; the staged scan checks the next
commit. Keep redaction enabled and do not upload raw scanner reports. A clean
scan establishes what these rules and the known-credential comparison checked;
it cannot prove that every possible secret format has been recognized.
