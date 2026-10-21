#!/usr/bin/env python3
"""Reproduce one paired-dot CRDC figure; no intermediate output files are written.

Run:
    python3 get_data.py
    python3 plot.py --statistic pooled
    python3 plot.py --statistic median
    python3 plot.py --self-test
Dependency: matplotlib==3.11.2 (tested); Python 3.10+.
Install, if needed: python3 -m pip install matplotlib==3.11.2

Inputs: ED's 2017-18 and 2023-24 public-use ZIPs, pinned in get_data.py.
Documentation and source URLs are recorded in get_data.py. The 2023-24 Appendix
Workbook, sheets A/B, defines the source files and fields. Endpoint School Form
questions DISC-12 (2017-18, p.85) and DISC-26 (2023-24, p.98) define days missed:
https://civilrightsdata.ed.gov/assets/for-researchers/school-form/2017-18-All-Schools-Form.pdf
https://civilrightsdata.ed.gov/assets/downloads/2023-24-crdc-school-form.pdf

Variables, in both releases:
  Enrollment.csv: SCH_ENR_{HI,AM,AS,HP,BL,WH,TR}_{M,F} = K-12/ungraded enrollment.
  Suspensions.csv: SCH_DAYSMISSED_{race}_{M,F} = regular-school-year student-days
    missed due to out-of-school suspension, excluding summer/intersession.
  School Characteristics.csv: SCH_GRADE_KG/G01..G12/UG, state, and justice flag.
  COMBOKEY is the validated within-release join key; never use SCHID alone.
  Justice flag: JJ in 2017-18; SCH_JUST in 2023-24.

Analysis decisions:
  * Repeated cross-sections, 50 states + DC; Puerto Rico and justice facilities
    excluded. Retain charter, alternative and special-education schools. Schools
    must offer K-12 or ungraded instruction; preschool counts are not used.
  * Use published M/F counts in BOTH years. All 2023-24 X counts are suppressed
    (-10); do not infer them or label M/F sums as complete all-student totals.
    Students may have been classified differently across years.
  * Within each year, require all 14 enrollment AND 14 days-lost cells to be
    observed/nonnegative. Thus all races use the same schools within a year,
    and missing outcomes never remain in the denominator. Samples can change
    across years. Coverage is relative to eligible schools with complete M/F
    enrollment, not to an independently estimated national population.
  * Exclude schools with zero total M/F enrollment. Flag, but retain, schools
    with positive days for a race with zero fall enrollment in pooled rates:
    students can enter later in the school year. Median rates exclude schools
    with zero enrollment for the particular racial group (undefined rate).
  * All negative reserve codes and explicit blank/NA markers are unavailable,
    NOT zero. A documented numeric zero remains zero. Do not use precomputed
    TOT_* fields, which may sum only available components.
    2017: -3 skip error, -5 action plan, -6 force certification, -8 EDFacts missing,
    -9 not applicable/skipped, -11 quality suppression.
    2023: -3 processing error, -5 action plan, -6 force certification,
    -7 indeterminable, -9 skipped, -10 EO suppression, -11 quality suppression,
    -13 missing directional-indicator skip logic.
  * Pooled rate = 100 * SUM(student-days) / SUM(enrollment). Median rate is the
    unweighted median of 100 * school student-days / school enrollment, using
    positive group enrollment; average the two middle rates for an even count.
    Both measure days per 100 students, not percent suspended.
  * Privacy perturbation, reporting errors and nonrandom missingness remain.
    No outlier trimming/imputation, sampling CIs, causal claims or extrapolation.
    Enrollment is a fall snapshot; days lost accumulate during the school year.

Coverage, exclusions, source checksums and the plotted values are printed to
stdout and embedded in the PNG metadata; no separate report is created.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from decimal import Decimal, InvalidOperation
import io
import json
import math
import os
from pathlib import Path
import re
from statistics import median
import sys
import tempfile
import unittest
import zipfile

sys.dont_write_bytecode = True
from get_data import DEFAULT_CACHE, SOURCES, archive_path, find_member, validate_archive

RACES = {
    "BL": "Black",
    "AM": "American Indian / Alaska Native",
    "TR": "Two or more races",
    "HP": "Native Hawaiian / other Pacific Islander",
    "HI": "Hispanic / Latino",
    "WH": "White",
    "AS": "Asian",
}
SEXES = ("M", "F")
STATES = set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split())
GRADES = ["SCH_GRADE_KG", *[f"SCH_GRADE_G{i:02d}" for i in range(1, 13)], "SCH_GRADE_UG"]
YEAR_SCHEMA = {
    "2017-18": {"justice": "JJ"},
    "2023-24": {"justice": "SCH_JUST"},
}
MISSING = {"", "NA", "N/A", "NAN", "NULL", "."}


def count_or_missing(value: str) -> int | None:
    """Keep zero, reject malformed counts, and never convert a reserve to zero."""
    value = value.strip()
    if value.upper() in MISSING:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"Unexpected nonnumeric count: {value!r}") from exc
    if not number.is_finite() or number != number.to_integral_value():
        raise ValueError(f"Expected a finite integer count: {value!r}")
    return None if number < 0 else int(number)


def count_columns(prefix: str) -> list[str]:
    return [f"{prefix}_{race}_{sex}" for race in RACES for sex in SEXES]


def read_table(archive: zipfile.ZipFile, basename: str, columns: list[str]) -> dict:
    """Read selected columns as strings, keeping IDs and validating unique keys."""
    raw = archive.read(find_member(archive, basename))
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")  # 2017 School Characteristics is Windows-1252.
    reader = csv.DictReader(io.StringIO(text, newline=""))
    headers = [h.strip().strip('"') for h in (reader.fieldnames or [])]
    if len(set(headers)) != len(headers):
        raise ValueError(f"Duplicate CSV headers in {basename}")
    reader.fieldnames = headers
    missing = set(columns) - set(headers)
    if missing:
        raise ValueError(f"{basename}: missing required columns {sorted(missing)}")
    records = {}
    for line, row in enumerate(reader, start=2):
        if None in row or any(row.get(c) is None for c in columns):
            raise ValueError(f"Malformed CSV record: {basename}, line {line}")
        key = row["COMBOKEY"].strip()
        if not re.fullmatch(r"[0-9A-Za-z]{12}", key):
            raise ValueError(f"Invalid COMBOKEY {key!r} in {basename}, line {line}")
        if key in records:
            raise ValueError(f"Duplicate COMBOKEY {key} in {basename}")
        records[key] = {column: row[column].strip() for column in columns}
    return records


def eligible(characteristics: dict, justice_column: str) -> bool:
    return (
        characteristics["LEA_STATE"] in STATES
        and characteristics[justice_column].casefold() == "no"
        and any(characteristics[g].casefold() == "yes" for g in GRADES)
    )


def paired_counts(enrollment: dict, suspension: dict) -> tuple[dict, dict] | None:
    """Complete-school sample across all seven races and both released sexes."""
    enr, days = {}, {}
    for race in RACES:
        e = [count_or_missing(enrollment[f"SCH_ENR_{race}_{sex}"]) for sex in SEXES]
        d = [count_or_missing(suspension[f"SCH_DAYSMISSED_{race}_{sex}"]) for sex in SEXES]
        if None in e or None in d:
            return None
        enr[race], days[race] = sum(e), sum(d)
    return enr, days


def summarize(enrollment: dict, suspension: dict, characteristics: dict, justice_column: str, statistic: str = "pooled") -> dict:
    keys = set(enrollment)
    if keys != set(suspension) or keys != set(characteristics):
        raise ValueError("The three input files have different school-key sets; refusing a lossy join")
    audit = Counter(input_schools=len(keys))
    codes = {"enrollment": Counter(), "days_lost": Counter()}
    included_enrollment = dict.fromkeys(RACES, 0)
    reference_enrollment = dict.fromkeys(RACES, 0)
    days_lost = dict.fromkeys(RACES, 0)
    school_rates = {race: [] for race in RACES}
    for key in sorted(keys):
        char = characteristics[key]
        if not eligible(char, justice_column):
            audit["outside_geography_justice_or_no_k12"] += 1
            continue
        audit["eligible_schools"] += 1
        if enrollment[key]["LEA_STATE"] != char["LEA_STATE"] or suspension[key]["LEA_STATE"] != char["LEA_STATE"]:
            raise ValueError(f"State disagrees across files for school {key}")
        enr_values = [count_or_missing(enrollment[key][c]) for c in count_columns("SCH_ENR")]
        for role, record, prefix in [("enrollment", enrollment[key], "SCH_ENR"), ("days_lost", suspension[key], "SCH_DAYSMISSED")]:
            for column in count_columns(prefix):
                if count_or_missing(record[column]) is None:
                    codes[role][record[column] or "blank"] += 1
        if None not in enr_values:
            audit["complete_enrollment_schools"] += 1
            for race in RACES:
                reference_enrollment[race] += sum(count_or_missing(enrollment[key][f"SCH_ENR_{race}_{sex}"]) for sex in SEXES)
        else:
            audit["incomplete_enrollment_schools"] += 1
        pair = paired_counts(enrollment[key], suspension[key])
        if pair is None:
            audit["excluded_incomplete_counts"] += 1
            continue
        enr, days = pair
        if sum(enr.values()) == 0:
            audit["excluded_zero_total_enrollment"] += 1
            continue
        if any(enr[race] == 0 and days[race] > 0 for race in RACES):
            audit["schools_positive_days_zero_race_fall_enrollment"] += 1
        audit["included_schools"] += 1
        for race in RACES:
            included_enrollment[race] += enr[race]
            days_lost[race] += days[race]
            if statistic == "median" and enr[race] > 0:
                school_rates[race].append(100 * days[race] / enr[race])
    if not audit["included_schools"]:
        raise ValueError("No usable schools after documented exclusions")
    groups = {}
    for race, label in RACES.items():
        n, base = included_enrollment[race], reference_enrollment[race]
        if n <= 0 or base <= 0:
            raise ValueError(f"No positive enrollment denominator for {label}")
        groups[race] = {
            "label": label, "enrollment": n, "days_lost": days_lost[race],
            "days_per_100": median(school_rates[race]) if statistic == "median" else 100 * days_lost[race] / n,
            "reference_enrollment": base, "coverage_percent": 100 * n / base,
        }
        if statistic == "median":
            groups[race].update(
                schools_positive_group_enrollment=len(school_rates[race]),
                schools_excluded_zero_group_enrollment=audit["included_schools"] - len(school_rates[race]),
            )
    result = {"audit": dict(audit), "unavailable_cells": {k: dict(v) for k, v in codes.items()}, "groups": groups}
    if statistic == "median":
        result["summary_statistic"] = (
            "Unweighted median across schools of 100 * (male + female suspension days) / "
            "(male + female enrollment), retaining only positive group enrollment after the "
            "original complete-school filters. For even school counts, average the two middle rates."
        )
    return result


def load_year(year: str, cache_dir: Path, statistic: str = "pooled") -> dict:
    path = archive_path(year, cache_dir)
    if not path.exists():
        raise FileNotFoundError(f"Missing {path}. Run get_data.py first with the same --cache-dir.")
    digest = validate_archive(path, year)
    justice = YEAR_SCHEMA[year]["justice"]
    with zipfile.ZipFile(path) as archive:
        enrollment = read_table(archive, "Enrollment.csv", ["COMBOKEY", "LEA_STATE", *count_columns("SCH_ENR")])
        suspension = read_table(archive, "Suspensions.csv", ["COMBOKEY", "LEA_STATE", *count_columns("SCH_DAYSMISSED")])
        characteristics = read_table(archive, "School Characteristics.csv", ["COMBOKEY", "LEA_STATE", justice, *GRADES])
    result = summarize(enrollment, suspension, characteristics, justice, statistic)
    result.update(year=year, source_url=SOURCES[year]["url"], sha256=digest)
    return result


def draw_figure(results: dict, output: Path, statistic: str = "pooled") -> None:
    """Create only the requested PNG, with an embedded reproducibility audit."""
    # Matplotlib's font/config cache stays outside the repository.
    with tempfile.TemporaryDirectory(prefix="ps158-matplotlib-") as config:
        os.environ["MPLCONFIGDIR"] = config
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            from matplotlib.lines import Line2D
            from matplotlib.ticker import MultipleLocator
        except ImportError as exc:
            raise RuntimeError("Install Matplotlib: python3 -m pip install matplotlib==3.11.2") from exc

        plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11, "axes.unicode_minus": False})
        fig = plt.figure(figsize=(12.8, 8.8), facecolor="#fbfaf7")
        ax = fig.add_axes([0.315, 0.28, 0.615, 0.49], facecolor="#fbfaf7")
        colors = {"2017-18": "#9a4f20", "2023-24": "#006b78"}
        # Both figures retain the original pooled figure's horizontal scale.
        values = [100 * g["days_lost"] / g["enrollment"] for r in results.values() for g in r["groups"].values()]
        largest = max(values)
        upper = math.ceil(largest / 10) * 10 + 10
        for y, race in enumerate(RACES):
            first = results["2017-18"]["groups"][race]["days_per_100"]
            last = results["2023-24"]["groups"][race]["days_per_100"]
            ax.plot([first, last], [y, y], color="#bcbab5", linewidth=2.3, zorder=1)
            for year, value in [("2017-18", first), ("2023-24", last)]:
                ax.scatter(value, y, s=82, marker="o" if year == "2017-18" else "D", color=colors[year], edgecolors="#fbfaf7", linewidth=0.8, zorder=3)
                # Different vertical positions keep close endpoint values legible.
                ax.annotate(f"{value:.1f}", (value, y), xytext=(0, 10 if year == "2017-18" else -17), textcoords="offset points", ha="center", va="bottom" if year == "2017-18" else "top", color=colors[year], fontsize=10, weight="bold")
        ax.set_yticks(range(len(RACES)), list(RACES.values()))
        ax.set_ylim(len(RACES) - 0.45, -0.65)
        ax.set_xlim(-upper * 0.025, upper)
        ax.xaxis.set_major_locator(MultipleLocator(20 if upper > 120 else 10))
        xlabel = "Median school-level days lost per 100 enrolled students" if statistic == "median" else "School days lost per 100 enrolled students"
        ax.set_xlabel(xlabel, labelpad=14, fontsize=12)
        ax.grid(axis="x", color="#e2e0da", linewidth=0.8)
        ax.set_axisbelow(True)
        ax.tick_params(axis="both", length=0, labelcolor="#484743")
        ax.tick_params(axis="y", pad=15, labelsize=11)
        for spine in ax.spines.values():
            spine.set_visible(False)

        fig.text(0.065, 0.94, "RACIAL DISPARITIES IN SCHOOL DISCIPLINE", fontsize=10, weight="bold", color="#006b78")
        fig.text(0.065, 0.889, "School days lost to out-of-school suspension", fontsize=23, weight="bold", color="#242622")
        fig.text(0.065, 0.847, "2017–18 and 2023–24  |  K–12 / ungraded public schools, 50 states + D.C.", fontsize=12, color="#55564e")
        fig.legend(handles=[Line2D([], [], color=colors[y], marker="o" if y == "2017-18" else "D", linestyle="None", markersize=8, label=y.replace("-", "–")) for y in results], loc="upper left", bbox_to_anchor=(0.307, 0.82), frameon=False, ncol=2, columnspacing=2.2, handletextpad=0.5)

        coverage = []
        for year, result in results.items():
            percentages = [g["coverage_percent"] for g in result["groups"].values()]
            coverage_range = "≥99.99%" if min(percentages) >= 99.99 else f"{min(percentages):.2f}–{max(percentages):.2f}%"
            coverage.append(f"{year.replace('-', '–')}: {result['audit']['included_schools']:,} schools; {coverage_range} of known eligible enrollment by race")
        notes = [
            "Scope: published male/female counts only. Nonbinary counts are suppressed in 2023–24; reporting categories changed.",
            "Each year uses schools with complete enrollment and days-lost counts for all seven groups. Justice facilities excluded.",
            "Coverage: " + coverage[0] + ".",
            "                  " + coverage[1] + ".",
            ("Rates are school medians, using positive group enrollment. Privacy perturbation and nonrandom missingness remain."
             if statistic == "median" else
             "Rates are ratios of summed days to summed enrollment. Privacy perturbation and nonrandom missingness remain."),
            "Source: U.S. Department of Education, CRDC public-use files; civilrightsdata.ed.gov/data. Full audit embedded in PNG metadata.",
        ]
        for i, note in enumerate(notes):
            fig.text(0.065, 0.175 - i * 0.023, note, fontsize=9, color="#55564e")
        output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output, dpi=180, facecolor=fig.get_facecolor(), metadata={
            "Title": ("Median school-level days lost" if statistic == "median" else "School days lost") + " to out-of-school suspension by race, 2017-18 and 2023-24",
            "Description": __doc__, "CRDC audit": json.dumps(results, sort_keys=True),
            "Software": f"Python {sys.version.split()[0]}; Matplotlib {matplotlib.__version__}",
        })
        plt.close(fig)


class AnalysisTests(unittest.TestCase):
    """Synthetic checks of decisions that materially affect the plotted rates."""

    @staticmethod
    def records(enrollment=10, days=2):
        e = {f"SCH_ENR_{r}_{s}": str(enrollment) for r in RACES for s in SEXES}
        d = {f"SCH_DAYSMISSED_{r}_{s}": str(days) for r in RACES for s in SEXES}
        e["LEA_STATE"] = d["LEA_STATE"] = "CA"
        c = {g: "No" for g in GRADES}
        c.update(LEA_STATE="CA", JJ="No", SCH_GRADE_KG="Yes")
        return {"a": e}, {"a": d}, {"a": c}

    def test_missing_codes_and_real_zero(self):
        for value in ["", "NA", "NaN", "-3", "-5", "-6", "-7", "-8", "-9", "-10", "-11", "-13"]:
            self.assertIsNone(count_or_missing(value))
        self.assertEqual(count_or_missing("0"), 0)
        self.assertEqual(count_or_missing("2.0"), 2)

    def test_malformed_counts_fail(self):
        for value in ["garbage", "1.5", "Infinity"]:
            with self.assertRaises(ValueError):
                count_or_missing(value)

    def test_partial_sex_counts_are_not_summed(self):
        e, d, c = self.records()
        d["a"]["SCH_DAYSMISSED_BL_F"] = "-11"
        self.assertIsNone(paired_counts(e["a"], d["a"]))

    def test_ratio_of_sums_and_nonbinary_exclusion(self):
        e, d, c = self.records(10, 10)
        e2, d2, c2 = self.records(90, 0)
        e["b"], d["b"], c["b"] = e2["a"], d2["a"], c2["a"]
        e["a"]["SCH_ENR_BL_X"] = d["a"]["SCH_DAYSMISSED_BL_X"] = "-10"
        result = summarize(e, d, c, "JJ")
        self.assertEqual(result["groups"]["BL"]["days_per_100"], 10)

    def test_missing_outcome_removes_denominator(self):
        e, d, c = self.records()
        e2, d2, c2 = self.records(100, 3)
        e["b"], d["b"], c["b"] = e2["a"], d2["a"], c2["a"]
        d["b"]["SCH_DAYSMISSED_WH_M"] = "-9"
        result = summarize(e, d, c, "JJ")
        self.assertEqual(result["audit"]["included_schools"], 1)
        self.assertEqual(result["groups"]["BL"]["enrollment"], 20)
        self.assertAlmostEqual(result["groups"]["BL"]["coverage_percent"], 100 / 11)
        median_result = summarize(e, d, c, "JJ", "median")
        self.assertEqual(median_result["audit"], result["audit"])
        self.assertEqual(median_result["groups"]["BL"]["days_per_100"], 20)

    def test_unweighted_median_even_and_odd_school_counts(self):
        e, d, c = self.records(10, 10)  # 100 days per 100 students.
        e2, d2, c2 = self.records(90, 0)  # Zero is an observed rate.
        e["b"], d["b"], c["b"] = e2["a"], d2["a"], c2["a"]
        self.assertEqual(summarize(e, d, c, "JJ")["groups"]["BL"]["days_per_100"], 10)
        self.assertEqual(summarize(e, d, c, "JJ", "median")["groups"]["BL"]["days_per_100"], 50)
        e3, d3, c3 = self.records(10, 2)  # 20 days per 100 students.
        e["c"], d["c"], c["c"] = e3["a"], d3["a"], c3["a"]
        self.assertEqual(summarize(e, d, c, "JJ", "median")["groups"]["BL"]["days_per_100"], 20)

    def test_median_excludes_zero_group_enrollment_only_for_that_group(self):
        e, d, c = self.records(10, 2)
        e2, d2, c2 = self.records(10, 10)
        e["b"], d["b"], c["b"] = e2["a"], d2["a"], c2["a"]
        for sex in SEXES:
            e["b"][f"SCH_ENR_BL_{sex}"] = "0"
        result = summarize(e, d, c, "JJ", "median")
        self.assertEqual(result["audit"]["included_schools"], 2)
        self.assertEqual(result["groups"]["BL"]["days_per_100"], 20)
        self.assertEqual(result["groups"]["BL"]["schools_positive_group_enrollment"], 1)
        self.assertEqual(result["groups"]["BL"]["schools_excluded_zero_group_enrollment"], 1)
        self.assertEqual(result["groups"]["WH"]["days_per_100"], 60)

    def test_zero_total_enrollment_excluded(self):
        e, d, c = self.records(0, 0)
        with self.assertRaisesRegex(ValueError, "No usable schools"):
            summarize(e, d, c, "JJ")

    def test_zero_fall_race_enrollment_is_not_an_automatic_error(self):
        e, d, c = self.records()
        e2, d2, c2 = self.records()
        e["b"], d["b"], c["b"] = e2["a"], d2["a"], c2["a"]
        for sex in SEXES:
            e["a"][f"SCH_ENR_BL_{sex}"] = "0"
        result = summarize(e, d, c, "JJ")
        self.assertEqual(result["audit"]["included_schools"], 2)
        self.assertEqual(result["audit"]["schools_positive_days_zero_race_fall_enrollment"], 1)
        self.assertEqual(result["groups"]["BL"]["days_lost"], 8)
        self.assertEqual(result["groups"]["BL"]["enrollment"], 20)

    def test_population_filters(self):
        e, d, c = self.records()
        self.assertTrue(eligible(c["a"], "JJ"))
        for change in [{"JJ": "Yes"}, {"JJ": "-5"}, {"LEA_STATE": "PR"}, {"SCH_GRADE_KG": "No"}]:
            self.assertFalse(eligible(c["a"] | change, "JJ"))

    def test_join_and_duplicate_keys_fail(self):
        e, d, c = self.records()
        with self.assertRaisesRegex(ValueError, "different school-key sets"):
            summarize(e, {}, c, "JJ")
        fake = io.BytesIO()
        with zipfile.ZipFile(fake, "w") as z:
            z.writestr("Enrollment.csv", "COMBOKEY\n010000201705\n010000201705\n")
        with zipfile.ZipFile(fake) as z:
            with self.assertRaisesRegex(ValueError, "Duplicate COMBOKEY"):
                read_table(z, "Enrollment.csv", ["COMBOKEY"])

    def test_archive_checksum_pin_rejects_modified_bytes(self):
        with tempfile.TemporaryDirectory(prefix="ps158-checksum-test-") as directory:
            path = Path(directory) / "modified.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("Enrollment.csv", "modified source data")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                validate_archive(path, "2017-18")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--statistic", choices=("pooled", "median"), default="pooled", help="Summary statistic (default: pooled)")
    parser.add_argument("--output", type=Path, help="Output PNG; defaults to the corresponding statistic's figure filename")
    parser.add_argument("--self-test", action="store_true", help="Run synthetic validation tests without creating files")
    args = parser.parse_args()
    if args.self_test:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(AnalysisTests)
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    if args.output is None:
        suffix = "_median" if args.statistic == "median" else ""
        args.output = Path(__file__).resolve().with_name(f"suspension_days_by_race{suffix}.png")
    if args.output.suffix.lower() != ".png":
        parser.error("--output must be a .png file; only one figure is produced")
    try:
        results = {}
        for year in SOURCES:
            print(f"Reading and validating {year} ...", flush=True)
            results[year] = load_year(year, args.cache_dir, args.statistic)
            print(json.dumps(results[year], indent=2), flush=True)
        draw_figure(results, args.output, args.statistic)
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Saved {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
