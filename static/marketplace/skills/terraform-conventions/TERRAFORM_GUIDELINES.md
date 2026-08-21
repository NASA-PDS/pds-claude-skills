# Terraform Development Guidelines

Status: Draft — supersedes and formalizes the earlier "Terraform Development Guidelines" wiki page.
Sources: HashiCorp official Terraform docs, AWS Prescriptive Guidance ("Best Practices for Using the Terraform AWS Provider," Aug 2025), and PDS/PDC's own proven conventions (marked `PDS convention`). Full citations at the bottom.

Canonical copy: `NASA-PDS/tf-sheriff`. This copy is bundled with the `terraform-conventions` Claude Code skill so the skill is self-contained; if the two ever drift, the standalone repo is authoritative.

This document is written to be read by both humans and AI coding agents. Two tiers:

- **Must-Have** — required in every repo. Enforce via code review and the validator script (`scripts/validate_terraform.py`, see [Enforcement](#enforcement)).
- **Should-Have** — recommended; the standing improvement backlog, not a merge blocker.

## Architecture context

- **pdc-cds-infra** owns shared/singleton CDS resources (Cognito, CloudFront, org-wide IAM roles, shared security groups). No other repo creates a competing copy of these.
- **Application repos** (registry, web-analytics, pdc-observability, future components) each own a `terraform/` directory for their own resources only, and consume shared infra by reading SSM parameters `pdc-cds-infra` publishes — never by reading its state directly.
- **IAM**: system-wide roles are defined centrally (without policies attached); application-specific policies are defined and attached in the owning app's repo.
- **Multi-tenant future**: treat `venue` (dev/test/prod) and, eventually, `tenant` as variables on one module — never copy-paste a repo per environment (see the `validate`/`validate-test`/`validate-devin` anti-pattern in the companion State of Terraform report).

## Must-Have checklist

| ID | Requirement |
|----|-------------|
| M1 | `terraform/` directory at repo root; shared/singleton infra only in `pdc-cds-infra` |
| M2 | `main.tf`, `variables.tf`, `outputs.tf`, `versions.tf`, `README.md` all present |
| M3 | Provider config isolated in `providers.tf`; reusable modules never configure a provider block |
| M4 | `modules/` kept flat (1–2 levels); no module that just wraps one resource |
| M5 | `backend.tf` (S3) on every deployable module — no local-state exceptions |
| M6 | State bucket = `pds-<venue>-infra`, supplied via `backend-<venue>.hcl` |
| M7 | State locking actually enabled (`use_lockfile = true` or a wired DynamoDB table) |
| M8 | State files, real `.tfvars`, `.terraform/` excluded via `.gitignore` |
| M9 | `required_version` + `required_providers` set, with an appropriate `~>` pin |
| M10 | `.terraform.lock.hcl` committed for every root module |
| M11 | Every variable/output has a `type`/description; no un-defaulted env-specific values get silent defaults |
| M12 | No hardcoded secrets, ARNs, or account IDs in committed `.tf` files |
| M13 | Cross-component interfaces published via `/pds/<component>/...` SSM convention |
| M14 | `snake_case`, singular, non-redundant resource naming |
| M15 | `default_tags` block present with the mandatory keys `tenant`, `venue`, `component`, `managedby`, `cicd` — all keys and values lowercase, using the PDS AWS Resource Tagging Strategy's accepted values, not placeholder literals |
| M16 | Cross-repo module sources pinned to a tag/commit ref — never a default branch |
| M17 | AWS auth via assumed IAM role / OIDC only — no static long-lived keys |
| M18 | Terraform's execution role follows least privilege |

## Should-Have checklist

| ID | Recommendation |
|----|-----------------|
| S1 | Real CI: blocking fmt/validate, `plan` posted on PR, `apply` gated behind approval via OIDC |
| S2 | TFLint (AWS ruleset) + Checkov in CI, non-blocking to start |
| S3 | Client-side pre-commit hooks (fmt, tflint, checkov) |
| S4 | Module README input/output tables generated with `terraform-docs` |
| S5 | `examples/` directory for any module reused by more than one consumer |
| S6 | Rebuild `template-repo-java` / `template-repo-python`'s `terraform/` to reflect the Must-Haves |
| S7 | Retire tutorial-boilerplate stub repos; consolidate the `validate*` duplicates |
| S8 | Tag `pds-tf-modules` modules with semver; move toward `terraform-aws-<name>` naming |
| S9 | Promote `pdc-cds-infra`'s `cloudfront/pds-main` into `pds-tf-modules` as a reusable module |
| S10 | CloudTrail logging + alerting on the Terraform state buckets |
| S11 | Sentinel/Checkov policy-as-code to auto-enforce tagging, naming, locking |
| S12 | PR template includes a Terraform reviewer checklist mirroring the validator's "not statically checked" list |
| S13 | Adopt a central tags module + explicit per-resource tagging (the org's recommended "Approach A") for resource types `default_tags` doesn't reliably cover (IAM policy attachments, ASG-launched EC2 instances, launch templates, ENIs/security groups) |

## Code examples

### Root module layout

```
terraform/
├── main.tf              # resources; calls to nested modules
├── variables.tf         # all input variables, typed + described
├── outputs.tf           # all outputs, described
├── versions.tf          # required_version + required_providers
├── providers.tf         # provider "aws" block only (root modules)
├── backend.tf           # empty backend "s3" {} block (M5)
├── backend-dev.hcl       # per-venue backend config (M6)
├── backend-test.hcl
├── backend-prod.hcl
├── README.md
├── tfvars/
│   ├── dev.tfvars.example
│   ├── test.tfvars.example
│   └── prod.tfvars.example
└── modules/              # local nested modules, kept flat (M4)
    └── <name>/
```

### Backend (M5, M6, M7)

```hcl
# backend.tf — committed, no environment-specific values
terraform {
  backend "s3" {
    # bucket/key/region/use_lockfile supplied via -backend-config=backend-<venue>.hcl
  }
}
```

```hcl
# backend-dev.hcl — per venue
bucket       = "pds-dev-infra"
key          = "registry/opensearch_serverless.tfstate"
region       = "us-west-2"
use_lockfile = true      # native S3 locking (Terraform >= 1.10)
encrypt      = true
```

```
terraform init -backend-config=backend-dev.hcl
```

### Version pinning (M9, M10)

```hcl
# versions.tf — root/deploying module: pin tightly
terraform {
  required_version = ">= 1.9.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"   # allows 6.x, blocks 7.0
    }
  }
}
```

```hcl
# versions.tf — reusable module: stay loose
terraform {
  required_version = ">= 1.9.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0, < 7.0"
    }
  }
}
```

### Variables & outputs (M11)

```hcl
variable "venue" {
  type        = string
  description = "Deployment venue: dev, test, or prod."

  validation {
    condition     = contains(["dev", "test", "prod"], var.venue)
    error_message = "venue must be one of: dev, test, prod."
  }
}

variable "component_name" {
  type        = string
  description = "Component name used to build the SSM parameter prefix."
  # no default — environment/component-specific, caller must supply
}

output "opensearch_endpoint" {
  description = "HTTPS endpoint of the OpenSearch domain."
  value       = aws_opensearch_domain.this.endpoint
}
```

### SSM interface (M13)

```hcl
# publish
locals {
  module_relative_path = replace(abspath(path.module), "/^.*\\/terraform\\//", "")
  ssm_prefix            = "/pds/${var.component_name}/${local.module_relative_path}"
}

resource "aws_ssm_parameter" "opensearch_endpoint" {
  name        = "${local.ssm_prefix}/opensearch_endpoint"
  type        = "String"
  value       = aws_opensearch_domain.this.endpoint
  description = "OpenSearch endpoint published for downstream consumers."
  tags        = local.tags
}
```

```hcl
# consume
data "aws_ssm_parameter" "observability_opensearch_endpoint" {
  name = "/pds/observability/opensearch/opensearch_endpoint"
}
# use: data.aws_ssm_parameter.observability_opensearch_endpoint.value
```

### Naming & tagging (M14, M15)

Mandatory tag values, per the PDS **AWS Resource Tagging Strategy** (all keys and values lowercase):

| Tag | Description | Example values |
|---|---|---|
| `tenant` | Owner discipline node | `en`, `img`, `atm` |
| `venue` | Deployment environment — **note:** this is the full env identifier, distinct from the short `dev`/`test`/`prod` code used in state bucket names (M6) | `pds-cds-dev`, `pds-cds-prod` (extend with a `-test` value if your venue set includes it) |
| `component` | Name of the GitHub repository where the Terraform is stored | `registry`, `nucleus`, `dum` |
| `cicd` | How the resource was deployed | `con` (AWS console), `cli` (AWS CLI), `iac`/`terraform`, `manual`, `cd` (full automation) |
| `managedby` | Owning team email or GitHub repo URL — never a literal like `"terraform"` | `pds-operator@jpl.nasa.gov`, `https://github.jpl.nasa.gov/PDSEN/registry-deploy` |
| `version` (optional) | Application version | — |

```hcl
# Good
resource "aws_s3_bucket" "logs" {
  bucket = "pds-${var.venue}-web-analytics-logs"   # short venue code, e.g. "dev" — see M6
}
# Avoid — repeats resource type in the local name
# resource "aws_s3_bucket" "logs_bucket" { ... }

provider "aws" {
  region = "us-west-2"
  default_tags {
    tags = {
      tenant    = "en"
      venue     = "pds-cds-${var.venue}"   # full tag-value form, e.g. "pds-cds-dev"
      component = var.component_name         # matches the GitHub repo name
      managedby = var.managed_by              # team email or repo URL, e.g. "pds-operator@jpl.nasa.gov"
      cicd      = "iac"
    }
  }
}
```

**Coverage gap:** `default_tags` does not reliably tag every resource type — known gaps include IAM policy attachments, ASG-launched EC2 instances (depending on config), launch templates, and some child resources (ENIs, security groups). The org's recommended fix is a central tags module with explicit per-resource tagging (Should-Have S13, reference: `pds-tf-modules`); until that's adopted org-wide, explicitly tag any resource in a gap category rather than relying on `default_tags` alone.

**Enforcement today:** AWS Resource Groups Tag Policies are active in MCP Dev (reporting-only — non-compliant resources are surfaced, not blocked) across a defined set of roughly 28 taggable resource types (S3 buckets, EC2/ECS/RDS resources, IAM roles, Lambda functions, SSM parameters, and others). Don't invent new tag keys for cost-allocation purposes — only a specific JPL-wide set of tags is enabled for AWS Cost Explorer allocation; stick to the mandatory set above unless a new key has been confirmed enabled centrally.

### Module sourcing (M16)

```hcl
# Avoid — tracks whatever is on the default branch today
module "s3_bucket" {
  source = "git@github.com:NASA-PDS/pds-tf-modules.git//terraform/modules/s3/bucket"
}

# Required — pinned to a released tag
module "s3_bucket" {
  source = "git@github.com:NASA-PDS/pds-tf-modules.git//terraform/modules/s3/bucket?ref=v1.2.0"
}
```

### CI auth (M17)

```yaml
permissions:
  id-token: write   # required for OIDC
  contents: read

steps:
  - uses: aws-actions/configure-aws-credentials@v4
    with:
      role-to-assume: arn:aws:iam::111122223333:role/terraform-execution
      aws-region: us-west-2
```

## Applying this across CDS infra vs. multi-tenant app repos

| Concern | pdc-cds-infra (shared) | Application repos |
|---|---|---|
| State key | `pds-<venue>-infra`, key prefixed by CDS component (`cognito/`, `cloudfront/`, `iam/`...) | `pds-<venue>-infra`, key prefixed by app/component name |
| Owns | Singleton cross-cutting resources | App-specific resources only |
| IAM | System-wide roles, no policies attached | App-specific policies attached to shared roles |
| Interface | Publishes to SSM under `/pds/<component>/...` | Reads from SSM; never reads pdc-cds-infra state directly |
| Venue/tenant | dev/test/prod today; add tenant as a variable, not a fork | Same — venue/tenant are variables, never copy-pasted repos |
| Change control | Highest blast radius — strictest review, first repo for blocking CI | Lower blast radius, but same Must-Have bar |

## Enforcement

Guidance alone doesn't hold — pair this document with:

1. **`scripts/validate_terraform.py`** — a static check runnable in CI/pre-commit/locally that mechanically verifies the checkable subset of the Must-Haves (M2, M3, M5–M12 partial, M14 partial, M15, M16) and Should-Haves (S1, S2, S3, S5) against a `terraform/` directory. Errors (Must-Have) fail the run; warnings (Should-Have) don't, unless `--strict`.
2. **TFLint + Checkov** (S2) for AWS-specific resource-level checks the validator doesn't attempt (security group rules, encryption settings, IAM policy shape).
3. **This `terraform-conventions` Claude Code skill** so any AI agent authoring or reviewing Terraform in these repos is primed with this document and runs the validator before calling work done.
4. **The org PR template's Terraform reviewer checklist** (S12) — the validator explicitly reports which Must-Haves it *cannot* check (ownership boundaries, semantic naming, runtime auth behavior, least-privilege IAM, promotion/backlog items). Those become an explicit human sign-off step on every PR that touches `terraform/`.

## References

**HashiCorp**
- [Standard Module Structure](https://developer.hashicorp.com/terraform/language/modules/develop/structure)
- [Style Guide](https://developer.hashicorp.com/terraform/language/style)
- [Module creation — recommended pattern](https://developer.hashicorp.com/terraform/tutorials/modules/pattern-module-creation)

**AWS Prescriptive Guidance — "Best Practices for Using the Terraform AWS Provider" (Aug 2025)**
- [Introduction](https://docs.aws.amazon.com/prescriptive-guidance/latest/terraform-aws-provider-best-practices/introduction.html)
- [Security](https://docs.aws.amazon.com/prescriptive-guidance/latest/terraform-aws-provider-best-practices/security.html)
- [Backend](https://docs.aws.amazon.com/prescriptive-guidance/latest/terraform-aws-provider-best-practices/backend.html)
- [Code base structure](https://docs.aws.amazon.com/prescriptive-guidance/latest/terraform-aws-provider-best-practices/structure.html)
- [Version management](https://docs.aws.amazon.com/prescriptive-guidance/latest/terraform-aws-provider-best-practices/version.html)

**PDS/PDC**
- Existing draft "Terraform Development Guidelines" wiki page
- **AWS Resource Tagging Strategy** (internal, PDSEN wiki space) — source of record for the mandatory tag keys/values in M15 and the tagging code examples above
- Companion "State of Terraform" report (empirical basis for every `PDS convention` citation)
