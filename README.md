# Racial disparities in school suspension days

This POLISCI 158 project examines racial disparities in school days lost to out-of-school suspensions in U.S. public schools between 2017–18 and 2023–24.

## Figures

- **`suspension_days_by_race.png`** compares pooled national rates by racial/ethnic group: 100 × total suspension days lost ÷ total enrolled students. This is my preferred primary measure because it reflects the national burden relative to student enrollment.
- **`suspension_days_by_race_median.png`** compares median school-level rates. For each group, each school's rate is 100 × suspension days lost ÷ enrollment; the median gives each school with positive group enrollment equal weight. It uses the same years, racial groups, base population filters, and visual style.

## Data sources

The data come from the U.S. Department of Education's [Civil Rights Data Collection (CRDC)](https://civilrightsdata.ed.gov/data). Official public-use downloads:

- [2017–18 ZIP](https://civilrightsdata.ed.gov/assets/ocr/docs/2017-18-crdc-data.zip)
- [2023–24 ZIP](https://civilrightsdata.ed.gov/assets/ocr/docs/2023-24-crdc-data.zip)

Both releases supply `Enrollment.csv`, `Suspensions.csv`, and `School Characteristics.csv`. The analysis joins schools using `COMBOKEY` and uses racial-group enrollment (`SCH_ENR_*`), suspension days lost (`SCH_DAYSMISSED_*`), state, grades served, and justice-facility status.

## Reproducibility

Use Python 3.10+ and Matplotlib 3.11.2 (the tested version). From the repository root, run:

```bash
python3 -m pip install matplotlib==3.11.2
python3 get_data.py
python3 plot.py --statistic pooled
python3 plot.py --statistic median
```

These commands download and verify the official archives, then recreate the pooled figure as `suspension_days_by_race.png` and the median figure as `suspension_days_by_race_median.png`. Running `python3 plot.py` without `--statistic` still produces the pooled figure. Use `--output PATH.png` to choose another output filename. Downloads are cached in the system temporary directory. To choose another cache, pass the same `--cache-dir PATH` to both scripts. Run the built-in checks with:

```bash
python3 plot.py --self-test
```

## Limitations

The analysis uses published male/female counts; nonbinary counts are suppressed in 2023–24. It includes eligible K–12/ungraded public schools in the 50 states and Washington, D.C., excluding justice facilities, preschool-only schools, schools with incomplete required counts, and schools with zero total enrollment. Missing or suppressed values are not treated as zero. Reporting errors, privacy adjustments, and changing school populations may affect comparisons. Enrollment is a fall snapshot, while suspension days accumulate throughout the school year. These descriptive comparisons cannot establish the causes of observed disparities.
