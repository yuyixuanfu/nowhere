"""Card 29: 一条命令全量体检 — unified health check.

Runs qa_geocode, qa_probe, qa_alignment (sub-types 1-4), qa_lqa (rule layer),
and pytest suite. Produces a combined report.

Usage:
    cd C:\\Users\\84989\\Desktop\\nowhere_repo
    python -m nowhere.health

Output: health_report.md (repo root) + console summary.
Total runtime target: <= 3 minutes (heavy probes run in parallel).
"""
from __future__ import annotations

import asyncio
import pathlib
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

# GBK console fix
sys.stdout.reconfigure(encoding="utf-8")

_REPO = pathlib.Path(__file__).resolve().parent.parent
_REPORT = _REPO / "health_report.md"


# ═══════════════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class Finding:
    """A single health check finding."""
    id: str
    source: str          # geocode / probe / alignment / lqa / tests
    level: str           # pass / fail / skip
    phenomenon: str
    reproduction: str = ""
    detail: str = ""

    @property
    def symbol(self) -> str:
        return {"pass": "✓", "fail": "✗", "skip": "S"}[self.level]


@dataclass
class SectionResult:
    """Results from one QA source."""
    source: str
    elapsed: float
    findings: list[Finding] = field(default_factory=list)

    @property
    def pass_count(self) -> int:
        return sum(1 for f in self.findings if f.level == "pass")

    @property
    def fail_count(self) -> int:
        return sum(1 for f in self.findings if f.level == "fail")

    @property
    def skip_count(self) -> int:
        return sum(1 for f in self.findings if f.level == "skip")


# ═══════════════════════════════════════════════════════════════════════
# Runners (one per QA source)
# ═══════════════════════════════════════════════════════════════════════

def _run_geocode() -> SectionResult:
    """Run qa_geocode.run_tests() and collect findings."""
    t0 = time.time()
    findings: list[Finding] = []
    try:
        from nowhere.tests import qa_geocode
        results = qa_geocode.run_tests()

        # Overall pass/fail
        total = results["total_tested"]
        mismatches = results["total_mismatches"]
        if mismatches == 0:
            findings.append(Finding(
                id="GEO-001", source="geocode", level="pass",
                phenomenon=f"全部 {total} 城市国家码正确",
            ))
        else:
            for m in results["mismatches"]:
                coords_str = f"({m['coords'][0]:.4f}, {m['coords'][1]:.4f})" if m["coords"] else "N/A"
                findings.append(Finding(
                    id=f"GEO-{m['place']}", source="geocode", level="fail",
                    phenomenon=f"{m['place']}: 期望 {m['expected_cc']}, 实际 {m['actual_cc']}",
                    reproduction=f"trace_lookup('{m['place']}') -> {coords_str} via {m['source']}",
                ))

        # Fuzhou analysis as info
        fz = results.get("fuzhou", {})
        if fz.get("winner"):
            w = fz["winner"]
            findings.append(Finding(
                id="GEO-FUZHOU", source="geocode", level="pass",
                phenomenon=f"福州解析正确: {w['name']} ({w['lat']}, {w['lon']})",
                detail=f"score={w['score']}, pop={w['pop']}",
            ))

    except Exception as e:
        findings.append(Finding(
            id="GEO-ERR", source="geocode", level="fail",
            phenomenon=f"qa_geocode 崩溃: {type(e).__name__}: {e}",
            reproduction="from nowhere.tests import qa_geocode; qa_geocode.run_tests()",
        ))

    return SectionResult(source="geocode", elapsed=time.time() - t0, findings=findings)


def _run_probe() -> SectionResult:
    """Run qa_probe lightweight probes and collect findings."""
    t0 = time.time()
    findings: list[Finding] = []
    try:
        from nowhere.tests import qa_probe

        # Reset module-level results
        qa_probe._results.clear()

        # Run all probes (these are lightweight, no network)
        qa_probe.main()

        # Collect results from the module-level list
        for r in qa_probe._results:
            level = "pass" if r["pass"] else "fail"
            findings.append(Finding(
                id=f"PRB-{r['probe'][:20]}", source="probe", level=level,
                phenomenon=f"[{r['chain']}] {r['probe']}: {r['actual']}",
                reproduction=r.get("evidence", ""),
                detail=f"expected: {r['expected']}",
            ))

    except Exception as e:
        findings.append(Finding(
            id="PRB-ERR", source="probe", level="fail",
            phenomenon=f"qa_probe 崩溃: {type(e).__name__}: {e}",
            reproduction="from nowhere.tests import qa_probe; qa_probe.main()",
        ))

    return SectionResult(source="probe", elapsed=time.time() - t0, findings=findings)


def _run_alignment() -> SectionResult:
    """Run qa_alignment sub-types 1-4 (fast, no network)."""
    t0 = time.time()
    findings: list[Finding] = []
    try:
        from nowhere.tests import qa_alignment

        # Sub-type 1: Cultural Region Rectangles
        try:
            mismatches, known_five = qa_alignment.audit_1_region_rectangles()
            if len(known_five) == 5:
                findings.append(Finding(
                    id="ALN-1-KNOWN5", source="alignment", level="pass",
                    phenomenon=f"文化区矩形: 已知5实锤全部复现 ({len(known_five)}/5)",
                ))
            else:
                findings.append(Finding(
                    id="ALN-1-KNOWN5", source="alignment", level="fail",
                    phenomenon=f"文化区矩形: 已知5实锤只复现 {len(known_five)}/5",
                    reproduction="qa_alignment.audit_1_region_rectangles()",
                ))
            total_mismatch = len(mismatches)
            other = total_mismatch - len(known_five)
            if other > 0:
                findings.append(Finding(
                    id="ALN-1-OTHER", source="alignment", level="fail",
                    phenomenon=f"文化区矩形: 额外 {other} 个城市错配 (总 {total_mismatch})",
                    reproduction="qa_alignment.audit_1_region_rectangles()",
                ))
        except Exception as e:
            findings.append(Finding(
                id="ALN-1-ERR", source="alignment", level="fail",
                phenomenon=f"审计1文化区矩形崩溃: {e}",
            ))

        # Sub-type 2: Key Drift
        try:
            drift = qa_alignment.audit_2_key_drift()
            confirmed = [d for d in drift if d.get("severity") == "实锤"]
            if confirmed:
                for d in confirmed[:5]:
                    findings.append(Finding(
                        id=f"ALN-2-{d['key'][:15]}", source="alignment", level="fail",
                        phenomenon=f"地名键漂移[{d['type']}]: {d['key']}",
                        reproduction=d.get("detail", ""),
                    ))
                if len(confirmed) > 5:
                    findings.append(Finding(
                        id="ALN-2-MORE", source="alignment", level="skip",
                        phenomenon=f"还有 {len(confirmed) - 5} 个实锤键漂移 (见 qa_alignment_report.md)",
                    ))
            else:
                findings.append(Finding(
                    id="ALN-2", source="alignment", level="pass",
                    phenomenon=f"地名键漂移: {len(drift)} 个发现, 无实锤",
                ))
        except Exception as e:
            findings.append(Finding(
                id="ALN-2-ERR", source="alignment", level="fail",
                phenomenon=f"审计2键漂移崩溃: {e}",
            ))

        # Sub-type 3: Country Codes
        try:
            cc_findings, _ = qa_alignment.audit_3_country_codes()
            confirmed = [f for f in cc_findings if f.get("severity") == "实锤"]
            if confirmed:
                for f in confirmed[:5]:
                    findings.append(Finding(
                        id=f"ALN-3-{f['code']}", source="alignment", level="fail",
                        phenomenon=f"国家码[{f['type']}]: {f['code']}",
                        reproduction=f.get("detail", ""),
                    ))
            else:
                findings.append(Finding(
                    id="ALN-3", source="alignment", level="pass",
                    phenomenon=f"国家码边界: {len(cc_findings)} 个发现, 无实锤",
                ))
        except Exception as e:
            findings.append(Finding(
                id="ALN-3-ERR", source="alignment", level="fail",
                phenomenon=f"审计3国家码崩溃: {e}",
            ))

        # Sub-type 4: Calendar Drift
        try:
            cal = qa_alignment.audit_4_calendar()
            confirmed = [f for f in cal if f.get("severity") == "实锤"]
            if confirmed:
                for f in confirmed[:3]:
                    findings.append(Finding(
                        id=f"ALN-4-{f.get('place', '?')[:10]}", source="alignment", level="fail",
                        phenomenon=f"历法漂移[{f['type']}]: {f.get('place', '?')}",
                        reproduction=f.get("detail", ""),
                    ))
            else:
                findings.append(Finding(
                    id="ALN-4", source="alignment", level="pass",
                    phenomenon=f"历法漂移: {len(cal)} 个发现, 无实锤",
                ))
        except Exception as e:
            findings.append(Finding(
                id="ALN-4-ERR", source="alignment", level="fail",
                phenomenon=f"审计4历法崩溃: {e}",
            ))

    except Exception as e:
        findings.append(Finding(
            id="ALN-ERR", source="alignment", level="fail",
            phenomenon=f"qa_alignment 导入崩溃: {type(e).__name__}: {e}",
            reproduction="from nowhere.tests import qa_alignment",
        ))

    return SectionResult(source="alignment", elapsed=time.time() - t0, findings=findings)


def _run_lqa_rule() -> SectionResult:
    """Run qa_lqa rule layer only (no LLM judge, no batch sampling)."""
    t0 = time.time()
    findings: list[Finding] = []
    try:
        from nowhere.tests import qa_lqa

        # Layer 1: batch sample (small: 5 places, 2 walk steps for speed)
        samples = qa_lqa.layer1_batch_sample(max_places=5, walk_steps=2)

        # Layer 2: rule-based checks
        rule_bugs = qa_lqa.layer2_rule_check(samples)

        if not rule_bugs:
            findings.append(Finding(
                id="LQA-RULE", source="lqa", level="pass",
                phenomenon=f"规则层: {len(samples)} 样本, 0 bug",
            ))
        else:
            # Group by severity
            by_sev: dict[str, list] = {}
            for b in rule_bugs:
                by_sev.setdefault(b.severity, []).append(b)

            for sev in ["S1", "S2", "S3", "S4"]:
                bugs = by_sev.get(sev, [])
                if not bugs:
                    continue
                for b in bugs[:3]:
                    findings.append(Finding(
                        id=b.id, source="lqa", level="fail",
                        phenomenon=f"[{sev}] {b.phenomenon}",
                        reproduction=b.reproduction,
                        detail=b.root_cause_guess,
                    ))
                if len(bugs) > 3:
                    findings.append(Finding(
                        id=f"LQA-{sev}-MORE", source="lqa", level="skip",
                        phenomenon=f"还有 {len(bugs) - 3} 个 {sev} bug (见 qa_lqa_report.md)",
                    ))

    except Exception as e:
        findings.append(Finding(
            id="LQA-ERR", source="lqa", level="fail",
            phenomenon=f"qa_lqa 规则层崩溃: {type(e).__name__}: {e}",
            reproduction="from nowhere.tests import qa_lqa; qa_lqa.layer1_batch_sample(5, 2)",
        ))

    return SectionResult(source="lqa", elapsed=time.time() - t0, findings=findings)


def _run_pytest() -> SectionResult:
    """Run pytest suite and collect pass/fail counts."""
    t0 = time.time()
    findings: list[Finding] = []
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "nowhere/tests", "-q", "--tb=line", "--no-header"],
            cwd=str(_REPO),
            capture_output=True,
            text=True,
            timeout=160,
            encoding="utf-8",
            errors="replace",
        )

        output = result.stdout + result.stderr

        # Parse pytest output: "X passed, Y failed, Z errors"
        import re
        passed = failed = errors = skipped = 0
        m = re.search(r"(\d+) passed", output)
        if m:
            passed = int(m.group(1))
        m = re.search(r"(\d+) failed", output)
        if m:
            failed = int(m.group(1))
        m = re.search(r"(\d+) error", output)
        if m:
            errors = int(m.group(1))
        m = re.search(r"(\d+) skipped", output)
        if m:
            skipped = int(m.group(1))

        total = passed + failed + errors
        if failed == 0 and errors == 0:
            findings.append(Finding(
                id="TEST-ALL", source="tests", level="pass",
                phenomenon=f"pytest: {passed} passed, {skipped} skipped",
            ))
        else:
            findings.append(Finding(
                id="TEST-SUMMARY", source="tests", level="fail",
                phenomenon=f"pytest: {passed} passed, {failed} failed, {errors} errors",
                reproduction=f"cd {_REPO} && python -m pytest nowhere/tests -q --tb=line",
                detail=output[-500:] if len(output) > 500 else output,
            ))
            # Extract failed test names
            for line in output.splitlines():
                if "FAILED" in line:
                    findings.append(Finding(
                        id=f"TEST-{line.strip().split('::')[-1][:30]}", source="tests", level="fail",
                        phenomenon=line.strip(),
                        reproduction=f"python -m pytest {line.split('::')[0]} -v",
                    ))

    except subprocess.TimeoutExpired:
        findings.append(Finding(
            id="TEST-TIMEOUT", source="tests", level="fail",
            phenomenon="pytest 超时 (>160s)",
            reproduction="python -m pytest nowhere/tests -q",
        ))
    except Exception as e:
        findings.append(Finding(
            id="TEST-ERR", source="tests", level="fail",
            phenomenon=f"pytest 运行失败: {type(e).__name__}: {e}",
            reproduction="python -m pytest nowhere/tests -q",
        ))

    return SectionResult(source="tests", elapsed=time.time() - t0, findings=findings)


# ═══════════════════════════════════════════════════════════════════════
# Parallel execution
# ═══════════════════════════════════════════════════════════════════════

async def _run_all_parallel() -> list[SectionResult]:
    """Run heavy probes in parallel using ThreadPoolExecutor.

    qa_probe and lqa_batch are the heaviest; run them in threads
    alongside the lighter geocode/alignment checks.
    pytest runs in a subprocess (already separate process).
    """
    loop = asyncio.get_event_loop()
    pool = ThreadPoolExecutor(max_workers=5)

    # Submit all tasks
    futures = {
        "geocode": loop.run_in_executor(pool, _run_geocode),
        "probe": loop.run_in_executor(pool, _run_probe),
        "alignment": loop.run_in_executor(pool, _run_alignment),
        "lqa": loop.run_in_executor(pool, _run_lqa_rule),
        "tests": loop.run_in_executor(pool, _run_pytest),
    }

    results: list[SectionResult] = []
    for name, fut in futures.items():
        try:
            r = await fut
            results.append(r)
        except Exception as e:
            results.append(SectionResult(
                source=name, elapsed=0,
                findings=[Finding(
                    id=f"{name.upper()}-ERR", source=name, level="fail",
                    phenomenon=f"{name} 并行执行异常: {e}",
                )],
            ))

    pool.shutdown(wait=False)
    return results


# ═══════════════════════════════════════════════════════════════════════
# Report generation
# ═══════════════════════════════════════════════════════════════════════

def _generate_report(sections: list[SectionResult], total_elapsed: float) -> str:
    """Generate the combined health report markdown."""
    all_findings = [f for s in sections for f in s.findings]
    total = len(all_findings)
    passed = sum(1 for f in all_findings if f.level == "pass")
    failed = sum(1 for f in all_findings if f.level == "fail")
    skipped = sum(1 for f in all_findings if f.level == "skip")

    lines: list[str] = []
    lines.append("# Nowhere Health Report")
    lines.append("")
    lines.append(f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Total items**: {total} | **Pass**: {passed} | **Fail**: {failed} | **Skip**: {skipped}")
    lines.append(f"**Total time**: {total_elapsed:.1f}s")
    lines.append("")

    # Per-source summary table
    lines.append("## Summary by Source")
    lines.append("")
    lines.append("| Source | Items | Pass | Fail | Skip | Time |")
    lines.append("|--------|-------|------|------|------|------|")
    for s in sections:
        lines.append(
            f"| {s.source} | {len(s.findings)} | {s.pass_count} | {s.fail_count} | {s.skip_count} | {s.elapsed:.1f}s |"
        )
    lines.append("")

    # Per-source detail sections
    for s in sections:
        lines.append(f"## {s.source.upper()}")
        lines.append("")
        if not s.findings:
            lines.append("No findings.")
            lines.append("")
            continue

        lines.append("| ID | Level | Phenomenon | Reproduction |")
        lines.append("|----|-------|------------|--------------|")
        for f in s.findings:
            phenom = f.phenomenon[:80].replace("|", "\\|")
            repro = f.reproduction[:80].replace("|", "\\|") if f.reproduction else "-"
            lines.append(f"| {f.id} | {f.symbol} | {phenom} | {repro} |")
        lines.append("")

        # Detail for failures
        failures = [f for f in s.findings if f.level == "fail"]
        if failures:
            lines.append(f"### {s.source.upper()} Failures Detail")
            lines.append("")
            for f in failures:
                lines.append(f"- **{f.id}**: {f.phenomenon}")
                if f.reproduction:
                    lines.append(f"  - Reproduction: `{f.reproduction}`")
                if f.detail:
                    lines.append(f"  - Detail: {f.detail}")
            lines.append("")

    # Footer: new confirmed bug types (empty for first run)
    lines.append("---")
    lines.append("")
    lines.append("## New Confirmed Bug Types")
    lines.append("")
    prev_path = _REPO / "health_report.md"
    if prev_path.exists():
        try:
            prev = prev_path.read_text(encoding="utf-8")
            # Simple diff: find fail IDs in current that are not in previous
            import re
            prev_ids = set(re.findall(r"\| (GEO|PRB|ALN|LQA|TEST)-\S+ \|", prev))
            curr_ids = set(re.findall(r"\| (GEO|PRB|ALN|LQA|TEST)-\S+ \|",
                                       "\n".join(lines)))
            new_ids = curr_ids - prev_ids
            if new_ids:
                for bid in sorted(new_ids):
                    # Find the finding
                    for f in all_findings:
                        if f.id == bid and f.level == "fail":
                            lines.append(f"- NEW: **{f.id}** — {f.phenomenon[:60]}")
                            break
            else:
                lines.append("No new bug types since last run.")
        except Exception:
            lines.append("Could not diff with previous report.")
    else:
        lines.append("First run — no previous report to diff.")
    lines.append("")

    return "\n".join(lines)


def _print_console_summary(sections: list[SectionResult], total_elapsed: float) -> None:
    """Print a compact summary to the console."""
    all_findings = [f for s in sections for f in s.findings]
    total = len(all_findings)
    passed = sum(1 for f in all_findings if f.level == "pass")
    failed = sum(1 for f in all_findings if f.level == "fail")

    print("")
    print("=" * 60)
    print("HEALTH CHECK SUMMARY")
    print("=" * 60)
    print(f"  Total: {total}  Pass: {passed}  Fail: {failed}")
    print(f"  Time:  {total_elapsed:.1f}s")
    print("")

    for s in sections:
        status = "OK" if s.fail_count == 0 else f"FAIL({s.fail_count})"
        print(f"  [{status:>10}] {s.source:<12} {len(s.findings)} items in {s.elapsed:.1f}s")

    print("")

    # Print failures
    failures = [f for f in all_findings if f.level == "fail"]
    if failures:
        print(f"  FAILURES ({len(failures)}):")
        for f in failures[:15]:
            print(f"    {f.symbol} [{f.source}] {f.id}: {f.phenomenon[:60]}")
        if len(failures) > 15:
            print(f"    ... and {len(failures) - 15} more (see health_report.md)")
    else:
        print("  ALL PASSED.")

    print("")
    print(f"  Report: {_REPORT}")
    print("=" * 60)


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

def main() -> int:
    """Run the full health check. Returns exit code (0=all pass, 1=failures)."""
    print("=" * 60)
    print("Nowhere Health Check — Card 29")
    print("=" * 60)

    t0 = time.time()

    # Run all checks in parallel
    sections = asyncio.run(_run_all_parallel())

    total_elapsed = time.time() - t0

    # Generate report
    report = _generate_report(sections, total_elapsed)
    _REPORT.write_text(report, encoding="utf-8")

    # Console summary
    _print_console_summary(sections, total_elapsed)

    # Exit code
    all_findings = [f for s in sections for f in s.findings]
    has_failure = any(f.level == "fail" for f in all_findings)
    return 1 if has_failure else 0


if __name__ == "__main__":
    sys.exit(main())
