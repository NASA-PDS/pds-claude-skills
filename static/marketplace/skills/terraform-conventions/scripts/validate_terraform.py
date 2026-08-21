#!/usr/bin/env python3
"""
validate_terraform.py — PDS/PDC Terraform Development Guidelines validator.

Statically checks a terraform/ directory tree against the mechanically-checkable
subset of the org's Must-Have (error) and Should-Have (warning) requirements —
see TERRAFORM_GUIDELINES.md for the full list and rationale. This is NOT a
substitute for `terraform validate`, TFLint, or Checkov: it checks
*organizational convention* (structure, state config, tagging, naming, pinning),
not HCL correctness or AWS-resource-level security posture. Run it alongside
those tools, not instead of them.

Usage:
    python3 validate_terraform.py [path/to/terraform]
    python3 validate_terraform.py [path] --json      # machine-readable
    python3 validate_terraform.py [path] --github     # ::error::/::warning:: annotations for GH Actions
    python3 validate_terraform.py [path] --strict     # Should-Have warnings also fail the run

Exit codes:
    0  no Must-Have (error) findings — Should-Have (warning) findings are reported
       but do not fail the run unless --strict is set
    1  one or more Must-Have failures (or, with --strict, any warning) found
    2  usage error / path not found

Ignoring a known exception:
    Add a `.tfvalidate-ignore` file next to the terraform/ directory (or at repo
    root) with one check ID per line, optionally scoped to a path:
        M6                          # ignore this check everywhere
        M15  opensearch_managed     # ignore only for a module path containing this substring
    Ignored checks are still reported, but as SKIPPED, and skip counts are
    called out separately from the pass/fail summary so ignores don't silently
    erode coverage over time.

Design notes:
    - Every deployable module is a directory that contains a main.tf.
    - Checks are best-effort static/regex checks on file text, not a real HCL
      parser, to avoid a hard dependency. False positives/negatives are possible
      on unusual formatting — read the flagged file before treating a FAIL as
      certain.
    - Items that require human or CI-tool judgment (e.g. M18 least-privilege
      IAM, S10 CloudTrail alerting) are NOT silently skipped — they are listed
      in the report as NOT CHECKED so the report never implies more coverage
      than it actually has.
"""
import argparse
import json
import re
import sys
from pathlib import Path

DOC_REF = "See TERRAFORM_GUIDELINES.md for full rationale and examples."
REQUIRED_TAG_KEYS = {"tenant", "venue", "component", "managedby", "cicd"}

NOT_STATICALLY_CHECKABLE = [
    ("M1", "terraform/ ownership boundary respected (shared infra only in pdc-cds-infra)"),
    ("M4", "modules/ tree stays flat and isn't a thin single-resource wrapper"),
    ("M13", "SSM outputs are actually consumed correctly by downstream components"),
    ("M14", "resource naming is semantically meaningful (only redundancy/casing is checked)"),
    ("M17", "AWS auth uses OIDC/assumed role at runtime, not just absence of a literal key"),
    ("M18", "least-privilege IAM on the Terraform execution role"),
    ("S9", "cloudfront/pds-main promoted into pds-tf-modules"),
    ("S10", "CloudTrail logging/alerting enabled on the state bucket"),
    ("S11", "Sentinel/Checkov policy-as-code enforcing this document"),
]


def read(path: Path) -> str:
    try:
        return path.read_text(errors="ignore")
    except OSError:
        return ""


def find_deployable_modules(root: Path):
    modules = []
    for main_tf in root.rglob("main.tf"):
        if "/.terraform/" in str(main_tf):
            continue
        modules.append(main_tf.parent)
    return sorted(set(modules))


def is_reusable_module(module_dir: Path, root: Path) -> bool:
    rel = module_dir.relative_to(root)
    return "modules" in rel.parts or "examples" in rel.parts


def load_ignores(root: Path):
    """Returns list of (check_id, path_substring_or_None)."""
    ignores = []
    for candidate in (root / ".tfvalidate-ignore", root.parent / ".tfvalidate-ignore"):
        if candidate.exists():
            for line in read(candidate).splitlines():
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                parts = line.split(None, 1)
                check_id = parts[0]
                scope = parts[1].strip() if len(parts) > 1 else None
                ignores.append((check_id, scope))
            break
    return ignores


def is_ignored(ignores, check_id, path_str):
    for cid, scope in ignores:
        if cid == check_id and (scope is None or scope in path_str):
            return True
    return False


class Report:
    def __init__(self, label, kind):
        self.label = label
        self.kind = kind
        self.checks = []  # (id, title, severity[error|warn], status[pass|fail|skip], detail)

    def add(self, check_id, title, severity, passed, detail="", ignored=False):
        status = "skip" if ignored else ("pass" if passed else "fail")
        self.checks.append((check_id, title, severity, status, detail))

    def findings(self, severity=None, status="fail"):
        return [c for c in self.checks if c[3] == status and (severity is None or c[2] == severity)]


def check_module(module_dir: Path, root: Path, ignores) -> Report:
    reusable = is_reusable_module(module_dir, root)
    label = str(module_dir.relative_to(root)) or "."
    r = Report(label, "reusable module" if reusable else "deployable module")

    def add(check_id, title, severity, passed, detail=""):
        r.add(check_id, title, severity, passed, detail, ignored=is_ignored(ignores, check_id, str(module_dir)))

    files = {f.name: f for f in module_dir.glob("*.tf")}
    hcl_files = list(module_dir.glob("*.tf"))
    all_text = "\n".join(read(f) for f in hcl_files)

    # M2 — standard file set (error)
    for fname in ("variables.tf", "outputs.tf", "versions.tf"):
        add("M2", f"{fname} present", "error", fname in files)
    add("M2", "README.md present", "error", (module_dir / "README.md").exists())

    # M3 — provider config isolation (error)
    provider_files = [f for f in hcl_files if re.search(r'^\s*provider\s+"aws"', read(f), re.M)]
    if reusable:
        add("M3", "reusable module does not configure a provider block", "error", len(provider_files) == 0,
            detail=f"provider block found in: {[f.name for f in provider_files]}" if provider_files else "")
    else:
        add("M3", "provider block present in a root module", "error", len(provider_files) > 0)

    # M5 — backend.tf present (error)
    has_backend = "backend.tf" in files or re.search(r'backend\s+"s3"', all_text)
    if reusable:
        add("M5", "reusable module correctly has no backend block", "error", not has_backend)
    else:
        add("M5", "backend.tf configures an S3 backend", "error", has_backend)

    # M6 — state bucket naming pattern (error, best effort)
    if not reusable:
        hcl_configs = list(module_dir.glob("backend-*.hcl")) + list(module_dir.glob("*.hcl"))
        checked_any, ok = False, True
        for hf in hcl_configs:
            m = re.search(r'bucket\s*=\s*"([^"]+)"', read(hf))
            if m:
                checked_any = True
                if not re.match(r"^pds-(dev|test|prod)-infra$", m.group(1)):
                    ok = False
        if checked_any:
            add("M6", "state bucket follows pds-<venue>-infra", "error", ok)

    # M7 — state locking actually wired (error)
    if not reusable and has_backend:
        combined = read(files.get("backend.tf", Path("/dev/null"))) + "\n" + \
            "\n".join(read(f) for f in module_dir.glob("*.hcl"))
        wired = bool(re.search(r'use_lockfile\s*=\s*true', combined) or
                     re.search(r'dynamodb_table\s*=\s*"[^"]+"', combined))
        add("M7", "state locking wired (use_lockfile or dynamodb_table set to a real value)", "error", wired,
            detail="only found in a comment, or not set at all" if not wired else "")

    # M9 — version pinning (error)
    versions_text = read(files.get("versions.tf", Path("/dev/null")))
    add("M9", "required_version set", "error", bool(re.search(r'required_version\s*=', versions_text)))
    add("M9", "required_providers block present", "error", bool(re.search(r'required_providers\s*\{', versions_text)))
    add("M9", "AWS provider version constraint set", "error", bool(re.search(r'version\s*=\s*"[~><=!\d]', versions_text)))

    # M10 — lock file committed (error)
    if not reusable:
        add("M10", ".terraform.lock.hcl committed", "error", (module_dir / ".terraform.lock.hcl").exists())

    # M11 — variables typed + described (error)
    vtext = read(files.get("variables.tf", Path("/dev/null")))
    var_blocks = re.findall(r'variable\s+"([^"]+)"\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}', vtext, re.S)
    if var_blocks:
        untyped = [n for n, b in var_blocks if not re.search(r'\btype\s*=', b)]
        undescribed = [n for n, b in var_blocks if not re.search(r'\bdescription\s*=', b)]
        add("M11", "all variables have a type", "error", len(untyped) == 0,
            detail=f"missing type: {untyped}" if untyped else "")
        add("M11", "all variables have a description", "error", len(undescribed) == 0,
            detail=f"missing description: {undescribed}" if undescribed else "")

    # M12 — no obvious hardcoded account ID / ARN literal (error)
    literal_account = re.findall(r'\b\d{12}\b', all_text)
    literal_arn = re.findall(r'arn:aws:[a-z0-9-]+:[a-z0-9-]*:\d{12}:', all_text)
    add("M12", "no hardcoded 12-digit AWS account ID literal", "error", len(literal_account) == 0,
        detail=f"found: {literal_account[:5]}" if literal_account else "")
    add("M12", "no hardcoded ARN with embedded account ID", "error", len(literal_arn) == 0,
        detail=f"found: {literal_arn[:5]}" if literal_arn else "")

    # M14 — naming redundancy (error, partial check only)
    redundant = re.findall(r'resource\s+"(aws_\w+)"\s+"(\w+)"', all_text)
    bad_names = [(rtype, rname) for rtype, rname in redundant
                 if rtype.split("aws_", 1)[-1].rstrip("s") in rname.lower()]
    if redundant:
        add("M14", "resource local names don't repeat the resource type", "error", len(bad_names) == 0,
            detail=f"e.g. {bad_names[:5]}" if bad_names else "")

    # M15 — default_tags block with standard keys (error)
    if provider_files:
        ptext = "\n".join(read(f) for f in provider_files)
        dt_match = re.search(r'default_tags\s*\{.*?tags\s*=\s*\{(.*?)\}', ptext, re.S)
        add("M15", "default_tags block present", "error", bool(dt_match))
        if dt_match:
            keys_found = set(re.findall(r'(\w+)\s*=', dt_match.group(1)))
            missing = REQUIRED_TAG_KEYS - keys_found
            add("M15", "default_tags has the standard key set (tenant/venue/component/managedby/cicd)",
                "error", len(missing) == 0, detail=f"missing keys: {sorted(missing)}" if missing else "")

    # M16 — module source pinning (error)
    module_sources = re.findall(r'source\s*=\s*"([^"]+)"', all_text)
    remote_unpinned = [s for s in module_sources
                        if ("git@" in s or "github.com" in s or "git::" in s) and "ref=" not in s]
    if module_sources:
        add("M16", "remote module sources are pinned with ?ref=", "error", len(remote_unpinned) == 0,
            detail=f"unpinned: {remote_unpinned}" if remote_unpinned else "")

    # S5 — examples/ dir for reusable modules (warning)
    if reusable and "modules" in module_dir.relative_to(root).parts:
        has_examples = any((p / "examples").exists() for p in [module_dir, *module_dir.parents[:3]])
        add("S5", "an examples/ directory exists for this reusable module", "warn", has_examples)

    return r


def check_repo_level(root: Path, ignores) -> Report:
    r = Report("repo-level", "repo")

    def add(check_id, title, severity, passed, detail=""):
        r.add(check_id, title, severity, passed, detail, ignored=is_ignored(ignores, check_id, str(root)))

    # M8 — .gitignore covers state/tfvars/.terraform (error)
    gi, cur = None, root
    for _ in range(4):
        candidate = cur / ".gitignore"
        if candidate.exists():
            gi = candidate
            break
        cur = cur.parent
    if not gi:
        add("M8", ".gitignore covers state/tfvars/.terraform", "error", False, "no .gitignore found within 4 parent levels")
    else:
        text = read(gi)
        needed = ["*.tfstate", ".terraform/", "*.tfvars"]
        missing = [p for p in needed if p not in text and p.strip("*/") not in text]
        add("M8", ".gitignore covers state/tfvars/.terraform", "error", len(missing) == 0,
            f"missing patterns in {gi}: {missing}" if missing else "")

    # S1 — CI actually wired (warning): a workflow exists AND isn't fully commented-out on the apply step
    repo_root = root.parent if root.name == "terraform" else root
    workflows = list((repo_root / ".github" / "workflows").glob("*.y*ml")) if (repo_root / ".github" / "workflows").exists() else []
    tf_workflows = [w for w in workflows if re.search(r'terraform', read(w), re.I)]
    if tf_workflows:
        live = any(re.search(r'^\s*run:\s*terraform apply', read(w), re.M) for w in tf_workflows)
        add("S1", "a Terraform CI workflow exists and runs `terraform apply` (not just commented out)",
            "warn", live, detail=f"found {[w.name for w in tf_workflows]}, but apply step looks disabled/absent" if not live else "")
    else:
        add("S1", "a Terraform CI workflow exists", "warn", False)

    # S2 — TFLint/Checkov config present (warning)
    has_tflint = (repo_root / ".tflint.hcl").exists()
    has_checkov = (repo_root / ".checkov.yaml").exists() or (repo_root / ".checkov.yml").exists()
    precommit = repo_root / ".pre-commit-config.yaml"
    precommit_active = False
    if precommit.exists():
        ptext = read(precommit)
        precommit_active = bool(re.search(r'tflint|checkov', ptext, re.I)) and not all(
            line.strip().startswith("#") for line in ptext.splitlines() if "tflint" in line.lower() or "checkov" in line.lower()
        )
    add("S2", "TFLint and/or Checkov configured (standalone config or active pre-commit hook)", "warn",
        has_tflint or has_checkov or precommit_active)

    # S3 — pre-commit hooks present at all (warning)
    add("S3", ".pre-commit-config.yaml present", "warn", precommit.exists())

    return r


def render_text(module_reports, repo_report, strict):
    lines = []
    all_reports = module_reports + [repo_report]
    for r in all_reports:
        lines.append(f"[{r.label}]  ({r.kind})")
        for check_id, title, severity, status, detail in r.checks:
            mark = {"pass": "PASS", "fail": "FAIL" if severity == "error" else "WARN", "skip": "SKIP"}[status]
            line = f"  {mark:4s} {check_id:4s} {title}"
            if status != "pass" and detail:
                line += f"  — {detail}"
            lines.append(line)
        lines.append("")

    lines.append("Not statically checked (requires human/CI judgment):")
    for check_id, title in NOT_STATICALLY_CHECKABLE:
        lines.append(f"  ....  {check_id:4s} {title}")
    lines.append("")

    errors = sum(len(r.findings("error", "fail")) for r in all_reports)
    warnings = sum(len(r.findings("warn", "fail")) for r in all_reports)
    skipped = sum(len(r.findings(None, "skip")) for r in all_reports)

    lines.append(f"SUMMARY: {errors} Must-Have error(s), {warnings} Should-Have warning(s), {skipped} skipped via .tfvalidate-ignore.")
    if errors:
        lines.append(f"RESULT: FAIL — {errors} Must-Have failure(s). {DOC_REF}")
    elif strict and warnings:
        lines.append(f"RESULT: FAIL (--strict) — {warnings} Should-Have warning(s). {DOC_REF}")
    else:
        lines.append(f"RESULT: PASS — no Must-Have failures.{' ' + str(warnings) + ' warning(s) to address when convenient.' if warnings else ''}")
    return "\n".join(lines)


def render_github(module_reports, repo_report):
    lines = []
    for r in module_reports + [repo_report]:
        for check_id, title, severity, status, detail in r.checks:
            if status == "fail":
                kind = "error" if severity == "error" else "warning"
                msg = f"[{check_id}] {title} ({r.label})"
                if detail:
                    msg += f" — {detail}"
                lines.append(f"::{kind}::{msg}")
    return "\n".join(lines)


def to_json(module_reports, repo_report):
    def dump(r):
        return {"label": r.label, "kind": r.kind,
                "checks": [{"id": c[0], "title": c[1], "severity": c[2], "status": c[3], "detail": c[4]} for c in r.checks]}
    return {
        "modules": [dump(r) for r in module_reports],
        "repo": dump(repo_report),
        "not_statically_checked": [{"id": i, "title": t} for i, t in NOT_STATICALLY_CHECKABLE],
    }


def main():
    ap = argparse.ArgumentParser(description="Validate a terraform/ tree against PDS/PDC guidelines.")
    ap.add_argument("path", nargs="?", default="terraform", help="Path to a terraform/ directory (default: ./terraform)")
    ap.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    ap.add_argument("--github", action="store_true", help="Emit GitHub Actions ::error::/::warning:: annotations")
    ap.add_argument("--strict", action="store_true", help="Should-Have warnings also fail the run (exit 1)")
    args = ap.parse_args()

    root = Path(args.path).resolve()
    if not root.exists():
        print(f"error: path not found: {root}", file=sys.stderr)
        return 2

    modules = find_deployable_modules(root)
    if not modules:
        print(f"error: no main.tf found under {root}", file=sys.stderr)
        return 2

    ignores = load_ignores(root)
    module_reports = [check_module(m, root, ignores) for m in modules]
    repo_report = check_repo_level(root, ignores)

    errors = sum(len(r.findings("error", "fail")) for r in module_reports + [repo_report])
    warnings = sum(len(r.findings("warn", "fail")) for r in module_reports + [repo_report])

    if args.json:
        out = to_json(module_reports, repo_report)
        out["summary"] = {"errors": errors, "warnings": warnings}
        print(json.dumps(out, indent=2))
    elif args.github:
        out = render_github(module_reports, repo_report)
        if out:
            print(out)
    else:
        print(render_text(module_reports, repo_report, args.strict))

    if errors or (args.strict and warnings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
