# Statistics reference

`cellquant.stats` runs the analyses of a GraphPad Prism "Column" table: descriptive statistics, normality tests, and comparisons of two columns. This page says what each one computes, the equation, when to use it and the smallest sample it accepts.

Notation: a column has n values x₁…xₙ; x̄ is the mean and s the standard deviation. When two columns are compared, A is the first column and B the second. Every difference is **B − A** and every ratio is **B/A**, as in Prism.

## Data tables

A column table has one column of numbers per group. A blank cell is missing data, not zero. Columns can have different lengths. In a **paired** table, row i of every column is the same subject (the same animal, image or experiment).

```python
from cellquant.stats import ColumnTable, column_statistics, compare
table = ColumnTable.from_long(objects, value="ki67_mean", group="genotype", order=["WT", "KO"])
result = compare(table, "welch_t", "WT", "KO")
result.to_frame().to_csv("welch.csv")      # the results sheet
result.values["difference"], result.values["ci_low"], result.values["ci_high"]
```

`from_long(..., subject="mouse")` lines rows up by subject and makes the table paired.

## P values and significance

The **P value** is the probability of a difference at least this large if the null hypothesis (no difference) were true. A small P value does not tell you that the difference is large or important. Use the confidence interval for that.

- A result is called **significant** when P < α. By default α = 0.05.
- **Two-tailed** P (the default) counts differences in either direction. **One-tailed** P (`alternative="greater"` for B > A, or `"less"`) is valid only if you chose the direction before you saw the data. When the data go the predicted way, the one-tailed P is half the two-tailed P. When they go the other way, it is 1 − P/2.
- Confidence intervals are always two-sided.

Stars are decided on the unrounded P, with ≤ at every cutoff, as Prism does. For example, P = 0.05000001 shows as 0.0500 but is ns.

| Style | ns | * | ** | *** | **** | P shown as |
|---|---|---|---|---|---|---|
| `GP` (Prism's default; also its only style before Prism 7) | > 0.05 | ≤ 0.05 | ≤ 0.01 | ≤ 0.001 | ≤ 0.0001 | 0.0123, <0.0001 |
| `APA` | > 0.05 | ≤ 0.05 | ≤ 0.01 | ≤ 0.001 | — | .012, <.001 |
| `NEJM` | > 0.05 | ≤ 0.05 | ≤ 0.01 | ≤ 0.001 | — | 0.012, <0.001 |

The "0.1234 / 0.0332 / 0.0021 / 0.0002" numbers seen in an old Prism dialog only showed how P values can be formatted. They are not star cutoffs.

`format_p(p, digits=4)` prints P to the given number of decimals. A value below that precision prints as "<0.0001", and P = 1 prints as ">0.9999".

## Column statistics (`column_statistics`), n ≥ 1

| Statistic | Definition | Needs |
|---|---|---|
| Mean | x̄ = Σx / n | n ≥ 1 |
| SD | s = √(Σ(x − x̄)² / (n − 1)), how far values spread around the mean | n ≥ 2 |
| SEM | s / √n, how precisely the mean is known | n ≥ 2 |
| CI of the mean | x̄ ± t(1 − α/2, n − 1) · SEM | n ≥ 2 |
| CV | 100 · s / x̄ (%) | n ≥ 2 |
| Percentiles | The value at rank (n + 1)p of the sorted data, interpolated between neighbouring ranks. If the rank falls outside 1…n, the min or max is used. This is Prism's documented method (Hyndman-Fan type 6). | n ≥ 1 |
| Median, quartiles | The 50th, 25th and 75th percentiles; 10th and 90th as well | n ≥ 1 |
| Geometric mean | exp(mean of ln x). Geometric SD factor: exp(SD of ln x). CI: exp(mean ln x ± t · SEM of ln x) | all x > 0 |
| Harmonic mean | n / Σ(1/x) | all x > 0 |
| Quadratic mean | √(Σx² / n) | n ≥ 1 |
| Skewness | G₁ = n / ((n−1)(n−2)) · Σ((x − x̄)/s)³. 0 means symmetric; a positive value means a long right tail. | n ≥ 3 |
| Kurtosis | G₂ = n(n+1) / ((n−1)(n−2)(n−3)) · Σ((x − x̄)/s)⁴ − 3(n−1)² / ((n−2)(n−3)). This is excess kurtosis: 0 for a Gaussian, positive for heavier tails. | n ≥ 4 |

Skewness and kurtosis are the sample-adjusted versions that Prism and Excel report.

With `hypothetical=μ₀`, two tests compare the column with μ₀:

- **One-sample t test** (n ≥ 2). t = (x̄ − μ₀) / SEM, with df = n − 1. Use it for roughly Gaussian data. It also reports R² = t² / (t² + df).
- **Wilcoxon signed-rank test** (n ≥ 1). It ranks |x − μ₀| and reports W = (sum of ranks above μ₀) − (sum of ranks below). It does not assume a Gaussian distribution. P is calculated the same way as for the Wilcoxon matched-pairs test (below).

## Normality tests (`normality`, `lognormality`)

These tests ask whether the values could have come from a Gaussian (normal) distribution. A small P is evidence that they did not. "Passed" (P > α) does not prove that the data are Gaussian, because small samples pass almost always. Large samples fail at deviations too small to matter.

| Test | Statistic | Min n |
|---|---|---|
| D'Agostino-Pearson omnibus | K² = Z(skewness)² + Z(kurtosis)², compared with χ² with 2 df (D'Agostino et al. 1990) | 8 |
| Shapiro-Wilk | W = (Σ aᵢ x₍ᵢ₎)² / Σ(x − x̄)², where the weights aᵢ come from normal order statistics (Royston's algorithm) | 3 |
| Anderson-Darling | A² = −n − (1/n) Σ (2i − 1)[ln Φ(zᵢ) + ln(1 − Φ(zₙ₊₁₋ᵢ))], where zᵢ = (x₍ᵢ₎ − x̄)/s. P comes from A²(1 + 0.75/n + 2.25/n²). | 8 |
| Kolmogorov-Smirnov | The largest gap between the data's cumulative distribution and a Gaussian with mean x̄ and SD s. P is the Lilliefors P by the Dallal-Wilkinson approximation, which is valid below 0.1. Above 0.1, P comes from a simulated table. | 5 |

Below the minimum n, the result says "n too small" instead of giving a number. `lognormality` runs the same tests on ln(x). Every value must be greater than 0. The base of the logarithm does not change the results. The Anderson-Darling and Kolmogorov-Smirnov tests need statsmodels (`pip install "cellquant[stats]"`).

## Comparing two columns (`compare(table, test, first, second)`)

| Key | Test | Data | Min n |
|---|---|---|---|
| `unpaired_t` | Unpaired t (pooled variance) | Two independent groups, roughly Gaussian, similar SDs | 3 in all, ≥ 1 per column |
| `welch_t` | Welch's t | Two independent groups, roughly Gaussian, SDs may differ | 2 per column |
| `lognormal_t` | Unpaired t on log values | Independent groups with lognormal data (all > 0) | as above |
| `paired_t` | Paired t | Matched rows, Gaussian differences | 2 pairs |
| `ratio_paired_t` | Ratio paired t | Matched rows where the change is a consistent ratio (all > 0) | 2 pairs |
| `mann_whitney` | Mann-Whitney U | Two independent groups, any distribution | 1 per column |
| `kolmogorov_smirnov` | Two-sample KS | Two independent groups: any difference in distribution | 1 per column |
| `wilcoxon` | Wilcoxon matched-pairs signed rank | Matched rows, any distribution | 1 pair |

Blank cells are left out. Unpaired tests drop blanks column by column. Paired tests drop any row with a blank in either column, and the result reports how many rows were dropped.

### t tests

- **Unpaired (pooled).** s_p² = ((n_A − 1)s_A² + (n_B − 1)s_B²) / (n_A + n_B − 2). SE = s_p √(1/n_A + 1/n_B). t = (x̄_B − x̄_A) / SE, with df = n_A + n_B − 2. The result also includes an **F test** of equal variances. F = larger s² / smaller s², and P = 2 · P(F′ > F). A small P suggests using Welch's test. Prism also puts the larger variance on top.
- **Welch.** SE = √(s_A²/n_A + s_B²/n_B). df = SE⁴ / ((s_A²/n_A)²/(n_A − 1) + (s_B²/n_B)²/(n_B − 1)), which is usually not a whole number.
- **Paired.** dᵢ = Bᵢ − Aᵢ. t = mean(d) / (SD(d)/√n), with df = n − 1. The result also reports Pearson's r between A and B and a one-tailed P for r > 0, where t = r√(n − 2)/√(1 − r²). A significant r shows that pairing was effective.
- **Ratio paired.** The paired t test on log₁₀(B/A). The result reports the geometric mean of the ratios, 10^mean(log₁₀ ratio), with its CI.
- **Unpaired on logs.** The unpaired t test on log₁₀ values. The result reports the geometric means and their ratio B/A with its CI.
- **CI of the difference.** difference ± t(1 − α/2, df) · SE.
- **R²** (eta squared) = t² / (t² + df). This is the fraction of the total variation explained by the group.

**Effect sizes.** These measure the difference in SD units, so you can compare them across studies.

| Name | Formula |
|---|---|
| Cohen's d | (x̄_B − x̄_A) / s_p |
| Hedges' g | J · d, with J = Γ(ν/2) / (√(ν/2) Γ((ν − 1)/2)) and ν = n_A + n_B − 2 (J ≈ 1 − 3/(4(n_A + n_B) − 9)). J removes d's small-sample bias. |
| Glass's Δ | (x̄_B − x̄_A) / s_A. It is scaled by the SD of the first column (the control). |
| Paired d_z, g_z | mean(d) / SD(d), and J(n − 1) · d_z |

### Rank tests

Rank tests replace the values with their ranks. Tied values get the average rank. These tests do not assume a Gaussian distribution, but they answer a slightly different question than the t tests.

- **Mann-Whitney.** U_B = R_B − n_B(n_B + 1)/2, the number of (A, B) pairs where B is larger (a tie counts ½). The result reports U = min(U_A, U_B), the rank sums and the two medians.
  - P is **exact**, ties included, when the smaller column has at most 100 values. This is Prism's rule. The exact distribution is counted over every way of splitting the observed ranks, with midranks doubled so they are whole numbers.
  - This engine also limits exact P to n_A·n_B ≤ 20 000, which takes about 2 s at 100 × 200. Prism would still be exact beyond that.
  - Otherwise P comes from the Gaussian approximation with σ² = (n_A n_B / 12)[(N + 1) − Σ(t³ − t)/(N(N − 1))], where t is the size of each group of ties. There is no continuity correction; `continuity=True` adds R's 0.5.
  - The **Hodges-Lehmann** difference is the median of all n_A·n_B differences Bⱼ − Aᵢ. Its CI uses order statistics [d₍k₎, d₍N+1−k₎]. Only some confidence levels can be reached, so k gives the achievable level **nearest** to the one asked for, which can be below it (Sheskin; Klotz). The CI is printed with the level it actually reached.
- **Wilcoxon matched pairs.** Rank |dᵢ|. W = (sum of positive ranks) − (sum of negative ranks). There are two ways to handle zero differences:
  - `zero_method="exclude"` (the default) drops them. This is Wilcoxon's original method.
  - `"pratt"` ranks them and then leaves their ranks out of the sums (Pratt 1959). Prism offers Pratt as an option from Prism 6 on.

  P is exact, ties included, for fewer than 200 pairs. Each nonzero rank gets a random sign. Otherwise P comes from the Gaussian approximation with σ² = n(n + 1)(2n + 1)/24 − Σ(t³ − t)/48, with no continuity correction. The result also reports the median of the differences, and the Hodges-Lehmann pseudo-median (the median of the Walsh averages (dᵢ + dⱼ)/2 with i ≤ j) with its CI at the nearest achievable level. Spearman's r_s with a one-tailed P shows whether pairing was effective, as in Prism.
- **Very small samples.** With an exact P, a rank test has a smallest possible P. For example, a two-tailed Mann-Whitney test at 3 vs 3 cannot go below 2/C(6, 3) = 0.10, and a Wilcoxon test on 5 pairs cannot go below 2/2⁵ = 0.0625. When this floor is above α, the result warns that the test cannot find a significant difference, and states the floor.
- **Kolmogorov-Smirnov (two sample).** D = maxₓ |F_A(x) − F_B(x)|, the largest gap between the two cumulative distributions. It detects any difference in shape, not just in location. P is exact for n_A·n_B < 10 000. With ties, this P is conservative.

## Results

Each analysis returns an `AnalysisResult` that holds:

- `sections`: the results-sheet rows, each a (label, value, formatted text).
- `warnings`
- `methods`: a sentence for a methods section.
- `values`: the raw numbers for plots.

`to_frame()` gives one table to save as CSV.

## Checked against

R 4.x documented outputs:

- `sleep`: paired, pooled and Welch t; `var.test`; both forms of `wilcox.test`.
- `mtcars`: Welch t and Mann-Whitney with unequal n and ties.
- The exact Wilcoxon examples of Hollander and Wolfe in R's `?wilcox.test`.

Excel's documented SKEW, KURT, PERCENTILE.EXC and QUARTILE.EXC examples.

The Shapiro-Wilk (1965) worked example.

Brute-force permutation counts for the exact P values.

The tests' published formulas: D'Agostino 1990, Anderson-Darling, Dallal-Wilkinson.

## Prism sources

These are pages in the Prism guide (https://www.graphpad.com/guides/prism/latest/statistics/):

- `stat_decimal_formatting_of_p_values.htm`, and GraphPad's FAQ on asterisks: P styles and stars.
- `stat_percentiles_and_the_median.htm`: percentiles.
- `how_the_unpaired_t_test_works2.htm`: the F test.
- `how_the_mann-whitney_test_works.htm`: Mann-Whitney.
- `how_the_wilcoxon_matched_pairs_test_works.htm` and `stat_pratt.htm`: Wilcoxon matched pairs and Pratt.

## Not yet checked against Prism itself

- Prism's default for Wilcoxon zero differences. The guide implies "exclude".
- How Prism makes the exact two-tailed P: this engine doubles the smaller tail.
- The sign of the Hodges-Lehmann difference: this engine uses B − A, and the guide words it as group 1 minus group 2.
- How Prism computes the CI when there are ties. This engine uses the order-statistic rule for untied data.
