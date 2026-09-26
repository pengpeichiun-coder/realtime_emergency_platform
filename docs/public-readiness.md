# Public repository privacy checks

This repository is a curated application snapshot with independent Git history.
Only the current tracked source tree was exported. No earlier branches, tags,
release records, PR metadata or private registry artifacts were imported.

## Scope

The export retains the application, migrations, tests, local containers,
monitoring, Terraform architecture and useful engineering documentation. It omits
private release reconciliation, phase bookkeeping, exact private-image rehearsal
configuration and the tests/workflows dedicated to those artifacts. Local
runtime configuration, caches, database files and Terraform state are excluded.

The owner's confirmed GitHub noreply identity is used for the initial commit.
Future contributors should review author/committer attribution before publication;
hosted merge metadata depends on GitHub account settings.

## Checks

```bash
make public-readiness
```

This requires Gitleaks and a full local Git checkout. The helper examines tracked
files only, checks local Markdown targets/heading anchors and PNG metadata, then
scans a temporary tracked-tree copy and all local Git refs for secrets. It rejects
machine-specific home paths, non-example/non-noreply emails, private key material,
runtime environment files and common secret/state file types. Scanner errors fail
closed. Reports are temporary; only findings counts and safe locations are printed.

Independently scan a clean `git archive HEAD` export with Trivy's secret scanner.
Do not scan generated `.env` or caches and then publish their raw diagnostic
reports. Review external URLs separately: a valid link is not proof that a reader
has access. Public runbook URLs target documentation included in this repository.

`.gitleaks.toml` extends all default rules. Its only two exceptions combine a
specific rule, exact file path and exact non-secret text: a schema description
about a token hash, and Terraform's `RDS_PASSWORD` JSON-key name mapping. They do
not exempt files or ignore actual password/token values. Tests guard these scopes.

No automated scanner proves the absence of every possible privacy issue. Review
new screenshots, binaries, URLs, configuration and commit metadata before pushing.

## Screenshots and third-party material

The seven PNG screenshots contain fictional incidents, units and demo accounts.
Their bytes were reused after visual inspection; PNG text/EXIF chunks and trailing
metadata are absent. Leaflet/OpenStreetMap attribution remains visible. Tile
availability during capture is disclosed in the README.

The frontend icons are repository-authored SVG code. Dependencies retain their
upstream licenses, including React-Leaflet's Hippocratic License 2.1; no assumption
that all dependencies are MIT is made. The committed RDS CA bundle is the public
[AWS trust store](https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem),
not private-key material. See [AWS certificate documentation](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.SSL.html).

## License and operational boundaries

No LICENSE file has been added; the owner has not selected a software license.
The repository is publicly viewable without claiming an open-source license.
No AWS resources have been created and no external LLM has been called. The
application remains a fictional portfolio demo requiring human review of AI output.
