---
name: terraform-conventions
description: Use whenever writing, editing, or reviewing Terraform (.tf/.hcl files, anything under a terraform/ directory) in a NASA-PDS repository. Enforces the org's Must-Have Terraform requirements (remote state, locking, tagging, naming, SSM interface, version pinning, IAM/auth) and Should-Have recommendations. Trigger on any task involving Terraform modules, backends, providers, variables, or AWS infrastructure-as-code in PDS/PDC repos.
---

# PDS/PDC Terraform Conventions

You are working in a repository that follows PDS/PDC's org-wide Terraform Development Guidelines. Full text with rationale, citations, and code examples: `TERRAFORM_GUIDELINES.md` (co-located with this skill; the canonical copy lives in `NASA-PDS/tf-sheriff`) — read it if you need the "why" behind a rule or a full code example.

## Before writing or editing any Terraform

Confirm the change satisfies every applicable item below. If it's a new module, all of them apply.

1. **Structure**: `terraform/` at repo root (or the correct module subdirectory); `main.tf`, `variables.tf`, `outputs.tf`, `versions.tf`, `README.md` present. Provider config only in `providers.tf`, only in root modules — never inside a reusable module.
2. **State**: every deployable module has a `backend.tf` (S3, no hardcoded values) plus a `backend-<venue>.hcl`. Bucket name is `pds-<venue>-infra`. Locking is actually wired (`use_lockfile = true` or a real DynamoDB table) — do not leave it as a comment.
3. **Versions**: `versions.tf` sets `required_version` and a `~>`-pinned (root) or ranged (reusable module) `required_providers` block. Commit `.terraform.lock.hcl`.
4. **Variables/outputs**: every variable has `type` + `description`. Environment-specific values (ARNs, account IDs, component names) get no default — the caller must supply them. Every output has a `description`.
5. **Secrets**: never hardcode an ARN, account ID, password, or API key in a `.tf` file. Use a variable, a data source, SSM, or Secrets Manager.
6. **SSM interface**: if this component's output needs to be consumed elsewhere, publish it to SSM at `/pds/${var.component_name}/${local.module_relative_path}/<name>` using the `local.ssm_prefix` pattern — don't wire a direct `terraform_remote_state` reference into another repo's state.
7. **Naming/tagging**: `snake_case`, singular, non-redundant resource names. Every `provider.tf` has a `default_tags` block with at least `tenant`, `venue`, `component`, `managedby`, `cicd` (this exact casing).
8. **Module sourcing**: any `module` block sourced from another repo (especially `pds-tf-modules`) is pinned with `?ref=<tag>` — never tracks a default branch.
9. **Auth**: Terraform/CI authenticates via an assumed IAM role or OIDC — never a static access key checked into config or a workflow file.
10. **Architecture boundary**: shared/singleton CDS resources (Cognito, CloudFront, org-wide IAM roles, shared security groups) belong only in `pdc-cds-infra`. If you're in an application repo (registry, web-analytics, pdc-observability, or a new component) and about to create one of those, stop — consume it via SSM instead of recreating it.

## After making a change

Run the validator before calling the work done:

```bash
python3 scripts/validate_terraform.py terraform/
```

(This skill ships its own copy under `scripts/validate_terraform.py`; a repo may also vendor its own at `scripts/validate_terraform.py` or wire it into a pre-commit hook — prefer the repo's local copy if one exists, since it may carry repo-specific `.tfvalidate-ignore` context.) It mechanically checks the structural/checkable subset of the Must-Haves above (backend presence, locking, version pins, tag block, variable descriptions, module ref pinning, `.gitignore` coverage) and exits non-zero on failure. If neither the repo nor this skill's bundled copy is usable for some reason, say so explicitly to the user — don't silently skip enforcement.

Also run, if configured in the repo: `terraform fmt -check`, `terraform validate`, and `tflint`/`checkov` if present.

## What NOT to do

- Don't invent a new state bucket naming pattern, tag key, or SSM path scheme — reuse the ones above even if an existing file in the repo does something different (that's likely one of the inconsistencies this convention is fixing, not a pattern to copy).
- Don't add a `dynamodb_table` reference to a comment and call locking "done" — it has to be a real value the backend actually uses, or use S3 native locking.
- Don't wrap a single AWS resource in a module just to have a module.
- Don't copy the `template-repo-java`/`template-repo-python` terraform/ boilerplate as-is — it predates these guidelines and is scheduled for a rebuild (Should-Have S6). Use the layout in `TERRAFORM_GUIDELINES.md` instead.

## When creating or reviewing a pull request

If the change touches `terraform/`, make sure the PR includes the Terraform reviewer checklist from the org PR template — it covers the Must-Haves this validator cannot check statically (repo ownership boundary, semantic naming quality, runtime OIDC/auth behavior, least-privilege IAM, and a few Should-Have backlog items). Don't treat a clean validator run as equivalent to that checklist being satisfied.

## Scope note

This skill governs Terraform authored going forward. It is not a mandate to refactor unrelated, unchanged Terraform in the same repo just because it's out of compliance — flag drift you notice, but scope changes to what the user actually asked for unless they ask for a compliance sweep.
