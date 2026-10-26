# Statistics reference

`cellquant.stats` runs the analyses of a a commercial package "Column" table: descriptive statistics, normality tests, comparisons of two columns, one-way ANOVA and its nonparametric and repeated-measures forms with multiple comparisons, P value adjustment and outlier identification. This page says what each one computes, the equation, when to use it and the smallest sample it accepts.

Notation: a column has n values x₁…xₙ; x̄ is the mean and s the standard deviation. When two columns are compared, A is the first column and B the second. Every difference is **B − A** and every ratio is **B/A**, as in a commercial package.

## QuantAnalysis: the statistics window

QuantAnalysis analyses and graphs CellQuant results the way a commercial package's Column workflow does. It opens as its own window.

**Open it.** In step 6, **Analyze…** (next to **Export results…**) opens it with the open experiment. **Analyze a results folder…** on the Start page and in step 1 opens a saved results folder (with `experiment.json`) or an export folder (with `image_summary.csv`), so earlier results can be analysed without going through the steps. Inside the window, **File → Open CellQuant results folder…** does the same. The window is made once and reused.

**Import.** The **Import CellQuant results** dialog builds a data table:

- **Measure**: a report's percent (`report_N_percent`, shown by its name, for example "% of target" or "% of segmented cells"), `total_objects`, `count_area`, or a per-object value such as `area` or `<marker>_mean`.
- **Dot =**: what one value is. **Biological replicate (N)** is the default whenever there is a replicate column (the deepest `Folder N`, as for `units_by_<unit>.csv`). An **image** or an **object** is a technical replicate (n).
- **Group by** a column of step 1 (`Folder 1`, `group`…), or for a per-object measure a marker's +/− call; drag the groups into order.
- **Approved images only** (on by default; an export folder does not record approvals).
- **Use suggested setup** sets the suggested analysis for image data (below).

A table imported from the open experiment follows it: change a cutoff or an approval and the table is marked out of date and imported again when you return to the window (or click **Refresh**). Editing a table by hand detaches it.

**Analyse.** **Analyze → Column statistics**, **Normality and lognormality tests**, and **Compare two columns…** (a commercial package's t test dialog: paired or unpaired, parametric or not, the test, tails, alpha, CI level, P value style). **Help me choose…** asks a commercial package's questions and fills the dialog. Each analysis is a results sheet with its warnings and a methods sentence; **Copy** and **Export results CSV…** save it. Edit the data table and its results and graphs update.

**Graph.** Column scatter, bar and dots, box and dots, violin and dots, before-after (paired), estimation plot (the difference and its CI on a right axis, from a t test) and SuperPlot. A graph linked to a comparison shows its P summary as a bracket. The format panel sets titles, Y range and log scale, error bars, colours, dot and font size, and the size in mm or inches. **Export graph…** saves SVG, PDF, EPS, PNG, JPEG or LZW-compressed TIFF at 300, 600 or 1200 dpi.

Graphs need matplotlib, and two normality tests need statsmodels: `pip install "cellquant[stats]"`. Without them the window says so and the rest works. **Help → Log…** lists what was done.

### Suggested analysis for image data

- Measure: % of target (marker-positive and target-positive cells over target-positive cells) or % of segmented cells (marker-positive over every segmented object).
- One value per biological replicate (N), pooled from its images (n): the sum of the replicate's counts over the sum of its denominators.
- A SuperPlot with the mean ± SD of the replicates.
- Two groups: Welch's t test with the difference and its 95% CI, and an estimation plot; or Mann-Whitney when a Gaussian distribution cannot be assumed (see small samples below). Three or more groups (coming in the next update): Kruskal-Wallis with Dunn's tests against chosen reference groups.

## Replicates: N and n

A **biological replicate** is an independent sample: one subject, one culture, one independently prepared sample. Its number is **N**. **Technical replicates** are repeated measurements of one sample: its images, fields or cells. Their number is **n**.

A claim about a condition is a claim about biological replicates, so tests compare one value per replicate. Cells or images of one sample share its biology and its handling, so they are not independent. Testing them as if they were (**pseudoreplication**) makes n large and P falsely small: 600 cells from one sample per group is still N = 1 per group. QuantAnalysis marks a table with one dot per object with a red warning, on the table, its results and its graphs.

Pooling a percent over a replicate's images:

    replicate percent = Σ counts ÷ Σ denominators × 100

so an image with more cells weighs more within its replicate. This is not the mean of the images' percents. A per-object value is averaged over every counted object of the replicate.

A **SuperPlot** shows both levels: each image as a small dot shaded by its replicate, each replicate's value as a large outlined marker in the same colour, and the mean ± SD of the replicates. The test uses the replicates.

## Error bars: SD, SEM and CI

- **SD** shows how much the values vary. It does not shrink with more replicates. Use it to show the spread.
- **SEM** = SD / √N shows how precisely the mean is known. It shrinks as N grows.
- **95% CI** of the mean = x̄ ± t(0.975, N − 1) · SEM: the range that would contain the true mean in 95% of experiments like this one. For N = 3, t = 4.30, so the CI is more than four SEMs wide on each side.
- **Median and interquartile range** (25th to 75th percentile): for skewed data, or next to a rank test.

Say in the legend which one is shown and what N is.

## Small samples and rank tests

An exact rank test has a smallest possible P, reached when the groups do not overlap at all:

| Test | Smallest two-tailed P | Example |
|---|---|---|
| Mann-Whitney, n_A and n_B | 2 / C(n_A + n_B, n_A) | 3 vs 3: 2/20 = 0.10; 4 vs 4: 2/70 = 0.029 |
| Wilcoxon matched pairs, n pairs | 2 / 2ⁿ | 5 pairs: 0.0625; 6 pairs: 0.031 |

So at 3 vs 3 a Mann-Whitney test can never reach P < 0.05. QuantAnalysis warns when the chosen test cannot reach α with your n. With so few replicates, a t test (if its assumptions are reasonable) or more replicates is the answer, not a different rank test.

## Data tables

A column table has one column of numbers per group. A blank cell is missing data, not zero. Columns can have different lengths. In a **paired** table, row i of every column is the same subject (the same biological replicate, image or experiment).

```python
from cellquant.stats import ColumnTable, column_statistics, compare
table = ColumnTable.from_long(objects, value="marker_mean", group="condition", order=["control", "treated"])
result = compare(table, "welch_t", "control", "treated")
result.to_frame().to_csv("welch.csv")      # the results sheet
result.values["difference"], result.values["ci_low"], result.values["ci_high"]
```

`from_long(..., subject="replicate")` lines rows up by subject and makes the table paired.

## P values and significance

The **P value** is the probability of a difference at least this large if the null hypothesis (no difference) were true. A small P value does not tell you that the difference is large or important. Use the confidence interval for that.

- A result is called **significant** when P < α. By default α = 0.05.
- **Two-tailed** P (the default) counts differences in either direction. **One-tailed** P (`alternative="greater"` for B > A, or `"less"`) is valid only if you chose the direction before you saw the data. When the data go the predicted way, the one-tailed P is half the two-tailed P. When they go the other way, it is 1 − P/2.
- Confidence intervals are always two-sided.

Stars are decided on the unrounded P, with ≤ at every cutoff, as a commercial package does. For example, P = 0.05000001 shows as 0.0500 but is ns.

| Style | ns | * | ** | *** | **** | P shown as |
|---|---|---|---|---|---|---|
| `GP` (a commercial package's default; also its only style before a commercial package 7) | > 0.05 | ≤ 0.05 | ≤ 0.01 | ≤ 0.001 | ≤ 0.0001 | 0.0123, <0.0001 |
| `APA` | > 0.05 | ≤ 0.05 | ≤ 0.01 | ≤ 0.001 | — | .012, <.001 |
| `NEJM` | > 0.05 | ≤ 0.05 | ≤ 0.01 | ≤ 0.001 | — | 0.012, <0.001 |

The "0.1234 / 0.0332 / 0.0021 / 0.0002" numbers seen in an old a commercial package dialog only showed how P values can be formatted. They are not star cutoffs.

`format_p(p, digits=4)` prints P to the given number of decimals. A value below that precision prints as "<0.0001", and P = 1 prints as ">0.9999".

## Column statistics (`column_statistics`), n ≥ 1

| Statistic | Definition | Needs |
|---|---|---|
| Mean | x̄ = Σx / n | n ≥ 1 |
| SD | s = √(Σ(x − x̄)² / (n − 1)), how far values spread around the mean | n ≥ 2 |
| SEM | s / √n, how precisely the mean is known | n ≥ 2 |
| CI of the mean | x̄ ± t(1 − α/2, n − 1) · SEM | n ≥ 2 |
| CV | 100 · s / x̄ (%) | n ≥ 2 |
| Percentiles | The value at rank (n + 1)p of the sorted data, interpolated between neighbouring ranks. If the rank falls outside 1…n, the min or max is used. This is a commercial package's documented method (Hyndman-Fan type 6). | n ≥ 1 |
| Median, quartiles | The 50th, 25th and 75th percentiles; 10th and 90th as well | n ≥ 1 |
| Geometric mean | exp(mean of ln x). Geometric SD factor: exp(SD of ln x). CI: exp(mean ln x ± t · SEM of ln x) | all x > 0 |
| Harmonic mean | n / Σ(1/x) | all x > 0 |
| Quadratic mean | √(Σx² / n) | n ≥ 1 |
| Skewness | G₁ = n / ((n−1)(n−2)) · Σ((x − x̄)/s)³. 0 means symmetric; a positive value means a long right tail. | n ≥ 3 |
| Kurtosis | G₂ = n(n+1) / ((n−1)(n−2)(n−3)) · Σ((x − x̄)/s)⁴ − 3(n−1)² / ((n−2)(n−3)). This is excess kurtosis: 0 for a Gaussian, positive for heavier tails. | n ≥ 4 |

Skewness and kurtosis are the sample-adjusted versions that a commercial package and a spreadsheet report.

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

- **Unpaired (pooled).** s_p² = ((n_A − 1)s_A² + (n_B − 1)s_B²) / (n_A + n_B − 2). SE = s_p √(1/n_A + 1/n_B). t = (x̄_B − x̄_A) / SE, with df = n_A + n_B − 2. The result also includes an **F test** of equal variances. F = larger s² / smaller s², and P = 2 · P(F′ > F). A small P suggests using Welch's test. a commercial package also puts the larger variance on top.
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
  - P is **exact**, ties included, when the smaller column has at most 100 values. This is a commercial package's rule. The exact distribution is counted over every way of splitting the observed ranks, with midranks doubled so they are whole numbers.
  - This engine also limits exact P to n_A·n_B ≤ 20 000, which takes about 2 s at 100 × 200. a commercial package would still be exact beyond that.
  - Otherwise P comes from the Gaussian approximation with σ² = (n_A n_B / 12)[(N + 1) − Σ(t³ − t)/(N(N − 1))], where t is the size of each group of ties. There is no continuity correction; `continuity=True` adds R's 0.5.
  - The **Hodges-Lehmann** difference is the median of all n_A·n_B differences Bⱼ − Aᵢ. Its CI uses order statistics [d₍k₎, d₍N+1−k₎]. Only some confidence levels can be reached, so k gives the achievable level **nearest** to the one asked for, which can be below it (Sheskin; Klotz). The CI is printed with the level it actually reached.
- **Wilcoxon matched pairs.** Rank |dᵢ|. W = (sum of positive ranks) − (sum of negative ranks). There are two ways to handle zero differences:
  - `zero_method="exclude"` (the default) drops them. This is Wilcoxon's original method.
  - `"pratt"` ranks them and then leaves their ranks out of the sums (Pratt 1959). a commercial package offers Pratt as an option from a commercial package 6 on.

  P is exact, ties included, for fewer than 200 pairs. Each nonzero rank gets a random sign. Otherwise P comes from the Gaussian approximation with σ² = n(n + 1)(2n + 1)/24 − Σ(t³ − t)/48, with no continuity correction. The result also reports the median of the differences, and the Hodges-Lehmann pseudo-median (the median of the Walsh averages (dᵢ + dⱼ)/2 with i ≤ j) with its CI at the nearest achievable level. Spearman's r_s with a one-tailed P shows whether pairing was effective, as in a commercial package.
- **Very small samples.** With an exact P, a rank test has a smallest possible P. For example, a two-tailed Mann-Whitney test at 3 vs 3 cannot go below 2/C(6, 3) = 0.10, and a Wilcoxon test on 5 pairs cannot go below 2/2⁵ = 0.0625. When this floor is above α, the result warns that the test cannot find a significant difference, and states the floor.
- **Kolmogorov-Smirnov (two sample).** D = maxₓ |F_A(x) − F_B(x)|, the largest gap between the two cumulative distributions. It detects any difference in shape, not just in location. P is exact for n_A·n_B < 10 000. With ties, this P is conservative.

## Comparing three or more columns (`one_way(table, test)`)

Notation: k groups (columns); group i has nᵢ values with mean ȳᵢ and SD sᵢ; N = Σnᵢ values in all; ȳ is the mean of all values. In a repeated-measures table, n subjects (rows) are each measured in all k columns.

| Key | Test | Data | Min n |
|---|---|---|---|
| `ordinary` | Ordinary one-way ANOVA | Independent groups, roughly Gaussian, similar SDs | 1 per group, N > k |
| `welch` (= `brown_forsythe`) | Welch's ANOVA and Brown-Forsythe ANOVA (both reported) | Independent groups, roughly Gaussian, SDs may differ | 2 per group, SD > 0 |
| `repeated_measures` | Repeated-measures ANOVA, with or without the Geisser-Greenhouse correction | Matched rows, no blanks | 2 subjects |
| `mixed` | Mixed-effects model (REML) | Matched rows with blanks | 2 subjects, more values than subjects + groups |
| `kruskal_wallis` | Kruskal-Wallis | Independent groups, any distribution | 1 per group |
| `friedman` | Friedman | Matched rows, any distribution | 2 complete rows |

```python
from cellquant.stats import ColumnTable, one_way, compact_letter_display
table = ColumnTable.from_long(per_replicate, value="mean_intensity", group="condition",
                              order=["Negative control", "Positive control", "Treated 1", "Treated 2"])
result = one_way(table, "ordinary", comparisons="control", control=["Negative control", "Positive control"])
[(c["reference"], c["label"], c["p_adjusted"], c["summary"]) for c in result.values["comparisons"]]
letters = compact_letter_display(one_way(table, "ordinary", comparisons="all"))
```

`groups=[...]` analyzes some of the columns. Blank cells are left out (for repeated measures, see below).

**Biological and technical replicates.** ANOVA and its relatives treat every value as an independent observation. If a group holds many technical replicates (n, for example many measurements made within one sample) from a few biological replicates (N), the P value is far too small. Average the technical replicates within each biological replicate first, and analyze the N values. A result warns when a group has more than 30 values.

### Ordinary one-way ANOVA (`ordinary_anova`)

- SS_between = Σ nᵢ(ȳᵢ − ȳ)², with k − 1 df. SS_within (residual) = Σᵢ Σⱼ (yᵢⱼ − ȳᵢ)², with N − k df. SS_total = SS_between + SS_within.
- MS = SS/df. F = MS_between / MS_within, and P = P(F(k − 1, N − k) > F).
- **R²** = SS_between / SS_total, the fraction of all variation explained by the groups (eta squared).
- The result has a commercial package's ANOVA table (SS, DF, MS, F, P for treatment, residual and total), the group means, SDs and SEMs, and two tests of equal SDs:
  - **Brown-Forsythe test**: the ordinary ANOVA F on zᵢⱼ = |yᵢⱼ − median of group i| (Levene's test centred on the median), with (k − 1, N − k) df. It is robust to non-Gaussian data. A small P suggests Welch's or Brown-Forsythe ANOVA.
  - **Bartlett's test** (corrected): K² = [(N − k) ln s_p² − Σ(nᵢ − 1) ln sᵢ²] / [1 + (Σ 1/(nᵢ − 1) − 1/(N − k)) / (3(k − 1))], compared with χ² with k − 1 df. It is sensitive to non-Gaussian data. Both need at least 2 values in every group.

### Welch's and Brown-Forsythe ANOVA (`welch_anova`)

Use these when the SDs may differ. a commercial package reports both; `values["p"]` is Welch's.

- **Welch's W.** wᵢ = nᵢ/sᵢ², ȳ_w = Σwᵢȳᵢ/Σwᵢ, Λ = Σ(1 − wᵢ/Σw)²/(nᵢ − 1). W = [Σwᵢ(ȳᵢ − ȳ_w)²/(k − 1)] / [1 + 2(k − 2)Λ/(k² − 1)], with df (k − 1, (k² − 1)/(3Λ)).
- **Brown-Forsythe F\*.** F\* = Σnᵢ(ȳᵢ − ȳ)² / Σ(1 − nᵢ/N)sᵢ². With cᵢ = (1 − nᵢ/N)sᵢ² / Σ(1 − nⱼ/N)sⱼ², the df are (k − 1, 1/Σ(cᵢ²/(nᵢ − 1))). With equal n, F\* equals the ordinary F, but with fewer df.

### Repeated-measures ANOVA (`repeated_measures_anova`)

Each row is one subject measured under every treatment (column).

- SS_total = SS_treatment + SS_subjects + SS_residual, with SS_treatment = n Σⱼ(ȳ.ⱼ − ȳ)² (k − 1 df), SS_subjects = k Σᵢ(ȳᵢ. − ȳ)² (n − 1 df), and SS_residual the rest ((n − 1)(k − 1) df).
- F = MS_treatment / MS_residual. **Was the matching effective?** F = MS_subjects / MS_residual, with (n − 1, (n − 1)(k − 1)) df.
- **R²** = SS_treatment / (SS_treatment + SS_residual) (partial eta squared). With two columns this equals the paired t test's R², and F = t².
- **Sphericity** means that the differences between every pair of columns have the same variance. The **Geisser-Greenhouse epsilon** measures departure from it: ε = (tr S\*)² / ((k − 1) tr(S\*²)), where S is the k × k covariance matrix of the columns and S\* = CSC its double-centred form (C = I − J/k). 1/(k − 1) ≤ ε ≤ 1, and ε = 1 means no departure. ε is always reported.
- `geisser_greenhouse=True` (the default, and a commercial package's recommended choice) multiplies both df by ε, which makes P larger. `False` assumes sphericity. `values` keeps both P values.
- **Blank cells.** Repeated-measures ANOVA needs complete rows. With `missing="mixed"` (the default, as in a commercial package) a row with a blank makes the analysis fit the mixed-effects model below. `missing="exclude"` drops those rows.

### Mixed-effects model (`mixed_effects_anova`)

value = group mean + subject effect + error, with the subject effect and the error Gaussian (a random intercept per row), fitted by restricted maximum likelihood (REML) with statsmodels' MixedLM. Every value that is present is used.

What this engine reports:

- **Fixed effect of treatment**: a Wald F test that all group means are equal. DFn = k − 1. DFd = (number of values) − (number of subjects) − (k − 1), the "between-within" df. The covariance of the group means is (X′V̂⁻¹X)⁻¹ at the REML variance estimates. With no blanks, F, df and P are those of repeated-measures ANOVA with sphericity assumed (checked in the tests).
- **Random effects**: the SD and variance of the subject effect and of the residual.
- **Was the matching effective?**: a likelihood-ratio test of the subject variance (REML log likelihoods, χ² with 1 df, which is conservative at the boundary).

What a commercial package does differently, or may: its guide does not state which denominator df it uses for the F test, and a commercial package can apply a Geisser-Greenhouse correction to the mixed model. This engine assumes compound symmetry (sphericity) for the mixed model and makes no correction. Treat the mixed-model P as close to, but not guaranteed equal to, a commercial package's.

### Kruskal-Wallis test (`kruskal_wallis`)

All N values are ranked together (ties get the average rank). With Rᵢ the rank sum of group i:

H = [12/(N(N + 1)) Σ Rᵢ²/nᵢ − 3(N + 1)] / [1 − Σ(t³ − t)/(N³ − N)],

where t is the size of each group of ties. P is **approximate**, from χ² with k − 1 df; the result says so. The result also reports the medians, rank sums and mean ranks.

- With a group of fewer than 5 values the χ² approximation can be poor, and the result warns.
- The smallest P the exact permutation test could give is (number of block orders with the largest H) / (N!/Πnᵢ!), found by trying every order of blocks of consecutive ranks. If it is not below α, the result warns that no data could be significant. For example, three groups of 2 cannot go below 6/90 = 0.067.

### Friedman test (`friedman`)

Values are ranked within each row (ties get the average rank), and Rⱼ is the rank sum of column j. With ties corrected as in R's `friedman.test`:

Q = 12 Σ(Rⱼ − n(k + 1)/2)² / [nk(k + 1) − Σ(t³ − t)/(k − 1)],

and P is approximate, from χ² with k − 1 df. Rows with a blank are left out. The exact test cannot give a P below (k!)^(1−n) (every row ranking the columns the same way). For example, 2 subjects and 3 treatments cannot go below 1/6. When that floor is not below α, the result warns.

## Multiple comparisons

Every one-way analysis takes:

- `comparisons`: `"all"` (every pair), `"control"` (each group against one or two reference groups), `"selected"` (the `pairs=[("A", "B"), ...]` you list), or `"trend"`.
- `method`: see the table below. The default is Tukey for all pairs, Dunnett for control, and Šidák for selected pairs. After Welch's ANOVA it is Games-Howell (all pairs) or Dunnett's T3. After Kruskal-Wallis or Friedman it is Dunn's test.
- `control`: one reference group, or a list of two (for example a negative and a positive control).
- `family`: with two references, `"per_reference"` (the default) makes each reference its own family of k − 1 comparisons, like running a vs.-control analysis once per reference. `"combined"` corrects all the comparisons as one family, which is stricter. The comparison between the two references is counted once. Dunnett's test needs one reference per family.

Every comparison row in `values["comparisons"]` has `label` ("A vs. B"), `group_1`, `group_2`, `reference` (the reference group, or None), `family`, `estimate_1`, `estimate_2`, `difference`, `se`, `df`, `statistic` and `statistic_name`, the unadjusted `p`, `p_adjusted` (or `q` for FDR methods), `ci_low` and `ci_high` (blank where the method has no CI), `significant`, and `summary` (stars from the adjusted P, in the chosen P style). `values["family_alpha"]`, `values["n_families"]` and `values["comparisons_per_family"]` describe the families.

- **Sign.** As in a commercial package, the row "A vs. B" reports A − B. Against a reference the row is "reference vs. group", so the difference is reference − group.
- **Significance.** A comparison is significant when its adjusted P < α (the family-wise alpha). For FDR methods it is a "discovery" when q ≤ Q.
- **Error term.** Ordinary ANOVA uses MS_residual with N − k df: SE = √(MS_residual (1/nᵢ + 1/nⱼ)). Repeated measures with sphericity assumed uses MS_residual with (n − 1)(k − 1) df: SE = √(2 MS_residual/n). With the Geisser-Greenhouse correction, each comparison uses only its two columns: SE = SD of the differences/√n, df = n − 1 (`rm_error="pooled"` or `"separate"` overrides this). The mixed model uses the fitted means, their covariance and its df.

| `method` | Use after | Statistic and adjustment | CI |
|---|---|---|---|
| `tukey` | ANOVA, RM, mixed; all pairs | q = √2 · abs(diff)/SE on the studentized range of k means. With unequal n this is Tukey-Kramer. | yes |
| `newman_keuls` | ANOVA; all pairs | The same q, on the range of the r means the pair spans (sorted by mean). Stepwise: a pair is significant only if every wider range containing it was. It does not control the family-wise error rate, and gives no adjusted P (as in a commercial package). | no |
| `dunnett` | ANOVA, RM, mixed; vs. control | t = diff/SE. Adjusted P = P(max abs(Tᵢ) ≥ abs(t)) over the k − 1 comparisons, a multivariate t with correlations √(nᵢnⱼ/((nᵢ + n₀)(nⱼ + n₀))) (two-sided). | yes |
| `sidak` | any; any goal | P_adj = 1 − (1 − P)^m. CI at level (1 − α)^(1/m) per comparison. | yes |
| `bonferroni` | any; any goal | P_adj = min(1, mP). CI at level 1 − α/m. | yes |
| `holm_sidak` | any; any goal | Step-down: sort P ascending; P_adj(r) = max over s ≤ r of 1 − (1 − P(s))^(m − s + 1). More powerful than Šidák. | no |
| `fisher_lsd` | any; any goal | The t test on the ANOVA error term, no correction. | yes (unadjusted) |
| `games_howell` | Welch; all pairs | SE = √(sᵢ²/nᵢ + sⱼ²/nⱼ), Welch's df per pair, q on the studentized range of k means. | yes |
| `dunnett_t3` | Welch; any goal | The same t and df, on the studentized maximum modulus of m comparisons. | yes |
| `tamhane_t2` | Welch; any goal | Welch's t test, Šidák-adjusted. | yes |
| `dunn` | Kruskal-Wallis, Friedman | z = (R̄ᵢ − R̄ⱼ)/SE; P_adj = min(1, mP). For Kruskal-Wallis SE² = [N(N + 1)/12 − Σ(t³ − t)/(12(N − 1))](1/nᵢ + 1/nⱼ). For Friedman, SE² = k(k + 1)/(6n), with mean ranks within rows. | no |
| `dunn_uncorrected` | Kruskal-Wallis, Friedman | The same z, no correction. | no |
| `bky`, `bh`, `by` | any | q values from the unadjusted P (Fisher's LSD, Welch t, or uncorrected Dunn's), as below. | no |

m is the number of comparisons in the family. The multivariate t probability for Dunnett is computed by Dunnett's (1955) one-factor integral: Gauss-Hermite over the shared normal factor, and Gauss-Legendre over the error SD (√(χ²_df/df)). Correlations without that structure (repeated measures without sphericity, the mixed model) fall back to scipy's quasi-Monte Carlo integration with a fixed seed (100 000 points, about 6 decimals for P; the CI uses 20 000 points). The studentized maximum modulus uses the same integral over the error SD.

**Choosing.** Tukey for all pairs, Dunnett against a control, and Šidák or Holm-Šidák for a few planned pairs. When SDs differ, use Games-Howell (best for larger n), Dunnett's T3 (better for small n) or Tamhane's T2. Fisher's LSD makes no correction and is only for one or two planned comparisons. Newman-Keuls is there for legacy analyses; prefer Tukey.

**Small samples.** Dunn's test has a smallest possible P (no ties): the most extreme mean-rank difference between groups of nᵢ and nⱼ is N − (nᵢ + nⱼ)/2 after Kruskal-Wallis, and k − 1 after Friedman. If even that cannot give an adjusted P below α, the result warns which comparisons can never be significant. For example, after Kruskal-Wallis with four groups of 2, none of the six Dunn comparisons can reach P < 0.05.

### Test for linear trend (`comparisons="trend"` or `trend=True`)

The groups are taken as equally spaced, in column order xᵢ = 1…k. With x̄_w = Σnᵢxᵢ/N and the contrast cᵢ = nᵢ(xᵢ − x̄_w):

- **Slope** = Σcᵢȳᵢ / Σnᵢ(xᵢ − x̄_w)². This is the least-squares slope of the values on column number.
- SS_trend = (Σcᵢȳᵢ)² / Σnᵢ(xᵢ − x̄_w)². F = SS_trend/MS_residual with (1, error df), and **R² for trend** = SS_trend/SS_total.
- **Nonlinear variation**: F = [(SS_between − SS_trend)/(k − 2)] / MS_residual, with (k − 2, error df). It asks whether the group means depart from a straight line.

It needs 3 or more groups and equal SDs. It also works after repeated measures (with that error term).

### FDR control and q values (`adjust_p`, and `method="bky"` etc.)

The **false discovery rate** is the expected fraction of "discoveries" that are false. Q is the FDR you accept (`alpha`, default 5%). The **q value** of a comparison is the smallest Q at which it would be a discovery.

- **Benjamini-Hochberg** (`bh`, 1995): with P sorted ascending, q₍ᵣ₎ = min over s ≥ r of mP₍ₛ₎/s. Valid for independent or positively dependent tests.
- **Benjamini-Yekutieli** (`by`, 2001): the BH q times Σᵢ₌₁…ₘ 1/i. Valid under any dependence, and more conservative.
- **Two-stage step-up of Benjamini, Krieger and Yekutieli** (`bky`, 2006; a commercial package's default). Stage 1 runs BH at Q′ = Q/(1 + Q) and finds r₁ discoveries. m₀ = m − r₁ estimates the number of true null hypotheses. Stage 2 runs BH at Q′m/m₀. The q values are BH q · (m₀/m) · (1 + Q), with m₀ = m when r₁ is 0 or m. These match statsmodels' `fdr_tsbky`. They depend on Q, and a comparison is a discovery when q ≤ Q.

After an ANOVA, FDR methods start from the unadjusted P values of Fisher's LSD (Welch's t after Welch's ANOVA, uncorrected Dunn's after a rank test), as a commercial package does.

### Analyze a stack of P values (`p_value_stack`)

Paste a list of P values (blanks are left out). Choose FDR (`bky`, `bh`, `by`, with Q = `alpha`) or a family-wise correction (`bonferroni`, `sidak`, `holm`, `holm_sidak`, with family-wise α = `alpha`). The result lists each P with its rank, its q or adjusted P, and whether it is a discovery or significant, plus the number of discoveries (and m₀ for the two-stage method).

### Compact letter display (`compact_letter_display`)

Letters summarise a set of pairwise comparisons: two groups share a letter exactly when they are **not** significantly different. Pass any result with `values["comparisons"]`, or a list of rows. The insert-absorb algorithm (Piepho 2004) starts with one letter for every group. For each significant pair, it splits every letter that holds both groups into two letters, one without each group. Then it drops letters that are contained in another letter. Letters are given in column order. A pair that was not compared counts as not different. For PlantGrowth with Tukey's test: ctrl "ab", trt1 "a", trt2 "b".

## Identifying outliers (`identify_outliers(table, method)`)

Each column is tested separately, and needs at least 3 values. The result lists the flagged values (row and value) in `values["outliers"]`. `values["cleaned"]` is a new table with those cells blank, so paired rows stay lined up. The original table is never changed. Remove an outlier only for a reason you can report (for example a failed biological replicate), and say so in the methods.

- **ROUT** (`"rout"`, Q = `q`, default 1%; Motulsky and Brown 2006). It can find several outliers at once.
  1. *Robust fit.* Starting from the median, the centre μ minimizes the Lorentzian (Cauchy) merit function Σ ln(1 + ((x − μ)/RSDR)²). The robust SD of the residuals is RSDR = (68.27th percentile of |x − μ|) × N/(N − 1). μ and RSDR are updated in turn until they stop changing.
  2. *Outliers.* Each value gets t = |x − μ|/RSDR and a two-tailed P from the t distribution with N − 1 df. Then the Benjamini-Hochberg rule is applied at Q: with P sorted ascending, the j most extreme values are outliers for the largest j with P₍ⱼ₎ ≤ jQ/N.

  *Limits.* This follows the paper's description, not a commercial package's code. The percentile is linearly interpolated, and details of a commercial package's robust fit may differ, so a borderline value can be flagged by one and not the other. Only a constant is fitted (a commercial package also uses ROUT for curve fits). If more than 68% of the values are identical, RSDR is 0 and nothing is flagged.
- **Grubbs' test** (`"grubbs"`, two-sided, α = `alpha`): G = max|x − x̄|/s. The value is an outlier when G > ((n − 1)/√n) √(t²/(n − 2 + t²)), where t is the upper α/(2n) point of the t distribution with n − 2 df (Grubbs 1969). The reported P is the matching bound P = min(1, 2n · P(T_{n−2} > t_G)), with t_G = √(n(n − 2)G²/((n − 1)² − nG²)). It finds at most one outlier.
- **Iterative Grubbs'** (`"iterative_grubbs"`): repeats Grubbs' test after removing each outlier, down to 3 values. Two similar outliers can mask each other, so that neither is found (see the tests). ROUT is better when there may be more than one.

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
- `PlantGrowth`: `aov` (SS, MS, F, P), `TukeyHSD`, `oneway.test` (Welch), `kruskal.test`, `bartlett.test` and `car::leveneTest` (Brown-Forsythe).
- `InsectSprays`: `aov`, `kruskal.test` (many ties) and `bartlett.test` (R's `?bartlett.test` example).
- `warpbreaks`: `aov`, and the `?TukeyHSD` example (with wool as a blocking factor).
- The Hollander and Wolfe examples in `?kruskal.test` (mucociliary efficiency) and `?friedman.test` (RoundingTimes).

Published tables: Dunnett's two-sided 5% critical values (df = ∞ and 10, 1 to 9 groups), and Grubbs' (1969) two-sided 5% critical values.

scipy and statsmodels: `scipy.stats.tukey_hsd` (unequal n), `scipy.stats.dunnett`, `levene`, `bartlett`, `kruskal`, `friedmanchisquare`; statsmodels `AnovaRM` and `multipletests` (Šidák, Bonferroni, Holm, Holm-Šidák, `fdr_bh`, `fdr_by`, `fdr_tsbky`).

Independent code in the tests: the Geisser-Greenhouse epsilon by orthonormal contrasts, a direct REML fit of the mixed model, Games-Howell, T2, Dunn's test and the trend test written out from their formulas, and full enumeration of the Kruskal-Wallis permutation distribution. Simulations: Dunnett's and the studentized maximum modulus probabilities (millions of draws), the size of Welch's ANOVA, and the false-positive rates of Grubbs' test and ROUT.

a spreadsheet's documented SKEW, KURT, PERCENTILE.EXC and QUARTILE.EXC examples.

The Shapiro-Wilk (1965) worked example.

Brute-force permutation counts for the exact P values.

The tests' published formulas: D'Agostino 1990, Anderson-Darling, Dallal-Wilkinson.

## a commercial package sources

These are pages in the a commercial package guide ():

- `stat_decimal_formatting_of_p_values.htm`, and a commercial package's FAQ on asterisks: P styles and stars.
- `stat_percentiles_and_the_median.htm`: percentiles.
- `how_the_unpaired_t_test_works2.htm`: the F test.
- `how_the_mann-whitney_test_works.htm`: Mann-Whitney.
- `how_the_wilcoxon_matched_pairs_test_works.htm` and `stat_pratt.htm`: Wilcoxon matched pairs and Pratt.
- `stat_choosing_test_one-way_anova.htm`, `stat_options_tab_one-way_anova.htm`: the one-way ANOVA family, Welch and Brown-Forsythe ANOVA, Geisser-Greenhouse, the mixed-effects model.
- `stat_options_tab_1wayanova.htm`, `stat_posttesttrend.htm`, `stat_how_the_dunn_method_for_nonpar.htm`, `stat_fishers_lsd.htm`: multiple comparisons, the trend test, Dunn's test, Fisher's LSD.
- `stat_how_to_analyzing_a_stack_of_p_.htm`: a stack of P values.
- `stat_how_to_removing_outliers.htm`, `stat_detecting_outliers_with_grubbs.htm`, `stat_comparing_the_grubbs_and_rout_.htm`: ROUT and Grubbs'.
- User guide `compact_letter_display.htm`.

## Not yet checked against a commercial package itself

- a commercial package's default for Wilcoxon zero differences. The guide implies "exclude".
- How a commercial package makes the exact two-tailed P: this engine doubles the smaller tail.
- The sign of the Hodges-Lehmann difference: this engine uses B − A, and the guide words it as group 1 minus group 2.
- How a commercial package computes the CI when there are ties. This engine uses the order-statistic rule for untied data.
- Anything about the ANOVA family below was checked against R, published tables and independent code, not against a commercial package's output:
  - The sign and wording of comparison rows ("A vs. B" = A − B; "reference vs. group" against a control).
  - Whether a commercial package's multiple comparisons after the Geisser-Greenhouse correction use only the two columns compared (this engine's default) or the pooled error term.
  - a commercial package's R² for repeated-measures ANOVA (this engine reports partial eta squared) and for the trend test (SS_trend/SS_total).
  - The mixed model's denominator df and a commercial package's Geisser-Greenhouse option for it (see above).
  - Whether a commercial package computes an exact P for Kruskal-Wallis or Friedman with small samples (this engine always uses χ²), and whether its Friedman statistic and Dunn's test correct for ties.
  - How a commercial package defines q for the two-stage FDR method (this engine matches statsmodels), and whether a discovery is q ≤ Q or q < Q.
  - The order in which a commercial package assigns compact letters, and the details of its ROUT fit (see the limits above).
