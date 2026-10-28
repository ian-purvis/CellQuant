# Statistics reference

Every number QuantAnalysis reports can be checked. Each analysis has a page that says, in plain words, what is done to your data, then how each number is calculated, with a small worked example you can redo by hand or with a calculator. **Run it with the engine** under an example runs it again and shows the page's numbers beside the engine's. **Open as a data table** puts the example into your project so you can run the analysis yourself and compare the results sheet line by line. The terms used on results sheets (P value, degrees of freedom, confidence interval…) have their own pages under Terms.

On a results sheet, **How is this calculated?** opens the page for that analysis, and double-clicking a row label opens the term it names. The full equations are in the statistics guide (Help → Statistics guide…); each page shows the matching part under **Full equations**.

## Checking a result

### How to check a result by hand

**In short:** Start from the worked example on the analysis's page, then repeat the same steps with your own numbers.

**What is done to your data:** QuantAnalysis reads the values in the data table, drops blank cells (a paired test or a Friedman test drops the whole row; a repeated-measures ANOVA with a blank fits a mixed-effects model that keeps every value), and runs the analysis on what is left. Nothing is rounded before the end: the results sheet shows 4 significant digits, but **Export results CSV…** saves the full values in the `value` column.

**How it's calculated:** To check a result:

- Open the page for the analysis (**How is this calculated?** on the results sheet).
- Read **What is done to your data**: which values are used, and what each one stands for (a biological replicate, an image or an object).
- Work through the worked example. Each step names the quantity it computes, and the results sheet shows most of them, so you can see where a difference first appears.
- Repeat with your own numbers. Means, SDs and sums of squares need only a calculator. For a P value, look the test statistic up in a table or use a distribution function (for example `scipy.stats.t.sf(t, df)` in Python, or `pt()` in R).
- Small differences in the last digit come from rounding the intermediate steps. Keep at least 6 digits while you work.

**Checked against:** The engine is tested against R, scipy and statsmodels, published tables and independent code. The statistics guide lists every check under "Checked against", and the analyses not yet compared with other packages under "Not yet checked against other packages".

**Learn more:** [NIST/SEMATECH e-Handbook of Statistical Methods](https://www.itl.nist.gov/div898/handbook/) (formulas with worked sample output), [NIST Statistical Reference Datasets](https://www.itl.nist.gov/div898/strd/) (data with certified results for checking software), [Handbook of Biological Statistics](https://www.biostathandbook.com/) (each test explained for biologists), [Seeing Theory](https://seeing-theory.brown.edu/) (interactive pictures of the basic ideas), [StatQuest](https://statquest.org/video-index/) (short videos explaining each method).

**Related:** [[P value]], [[Degrees of freedom]], [[Confidence interval]]

## Analyses

### Describing columns

#### Column statistics

<!-- opens: colstats -->
<!-- equations: Column statistics -->

**In short:** Summarizes each column of numbers: how many values, where the middle is, how spread out they are, and how well the mean is known.

**When to use it:** Always, as a first look at every column before you test anything. It shows the n behind each group, whether the mean and median agree, and whether the spread is similar between groups. It does not compare groups; for that use a test such as the [[Unpaired t test]].

**What is done to your data:** Each column is summarized on its own. Blank cells are left out, and the number of values is the count of the cells that remain. Nothing is removed, transformed or ranked. The geometric and harmonic means need every value to be greater than 0 and stay blank otherwise. Each value should be one biological replicate if you want the SD, SEM and CI to describe your N (see [[N and n]]).

**How it's calculated:** For a column of n values x₁…xₙ, in order of the sheet:

- Number of values n, minimum, maximum, range = maximum − minimum, and the sum.
- [[Mean]] x̄ = Σx / n.
- [[Standard deviation]] s = √(Σ(x − x̄)² / (n − 1)). It needs n ≥ 2.
- [[SEM|Standard error of the mean]] = s / √n.
- The 95% [[Confidence interval|confidence interval]] of the mean is x̄ ± t* · SEM, where t* is the 97.5th percentile of the t distribution with n − 1 [[Degrees of freedom|df]]. The level can be 90%, 95% or 99%.
- [[Coefficient of variation]] = 100 · s / x̄, in percent.
- [[Percentiles and quartiles]]: the p-th percentile is the value at rank (n + 1)p of the sorted data, interpolated linearly between neighbouring ranks. If that rank falls below 1 or above n, the minimum or maximum is used. This is the Hyndman-Fan type 6 rule. The [[Median]] is the 50th percentile; the sheet also lists the 10th, 25th, 75th and 90th.
- [[Geometric mean]] = exp(mean of ln x). The geometric SD factor is exp(SD of ln x). The CI of the geometric mean is exp(mean of ln x ± t* · SEM of ln x).
- Harmonic mean = n / Σ(1/x). Quadratic mean = √(Σx² / n).
- Skewness G₁ = n / ((n − 1)(n − 2)) · Σ((x − x̄)/s)³, needing n ≥ 3. It is 0 for a symmetric column and positive when the right tail is longer.
- Kurtosis (excess) G₂ = n(n + 1) / ((n − 1)(n − 2)(n − 3)) · Σ((x − x̄)/s)⁴ − 3(n − 1)² / ((n − 2)(n − 3)), needing n ≥ 4. It is 0 for a [[Gaussian distribution]] and positive for heavier tails.

**Worked example:** One column of five values that grow by doubling.

```example
A: 1, 2, 4, 8, 16
>>> column_statistics(data["A"])
n = 5
median = 4
p25 = 1.5
p75 = 12
mean = 6.2
sd = 6.099
sem = 2.728
ci_low = -1.373
ci_high = 13.77
cv_percent = 98.37
geomean = 4
geosd_factor = 2.992
skewness = 1.325
```

By hand:

- Mean = 31 / 5 = 6.2. The deviations are −5.2, −4.2, −2.2, 1.8, 9.8, and their squares add up to 148.8, so s² = 148.8 / 4 = 37.2 and s = 6.099.
- SEM = 6.099 / √5 = 2.728. For 4 df, t* = 2.776, so the CI is 6.2 ± 2.776 · 2.728 = −1.373 to 13.77. In Python, `scipy.stats.t.ppf(0.975, 4)`; in R, `qt(0.975, 4)`.
- CV = 100 · 6.099 / 6.2 = 98.37%.
- Median: rank (5 + 1) · 0.5 = 3, the third value, 4. The 25th percentile has rank 6 · 0.25 = 1.5, halfway between 1 and 2, so 1.5. The 75th has rank 4.5, halfway between 8 and 16, so 12.
- Geometric mean: ln of the values is 0, 0.693, 1.386, 2.079, 2.773, whose mean is 1.386, and e^1.386 = 4. The SD of those logs is 0.693 · 1.581 = 1.096, and e^1.096 = 2.992.
- Skewness: the z scores (deviation / 6.099) are −0.853, −0.689, −0.361, 0.295, 1.607. Their cubes add up to 3.181, and 5 / (4 · 3) · 3.181 = 1.325.

**Interpreting the results:** The **mean and SD** describe the centre and spread of a roughly symmetric column. The **SEM and CI** say how precisely the mean is known, and shrink as n grows; the SD does not. If the **median** is far from the mean, or the **skewness** is far from 0, the column is lopsided and the mean and SD can mislead; look at the graph and consider the geometric mean or a log transform. A **CV** is useful only for values on a ratio scale (a true zero). In the example the SD is almost as large as the mean and the skewness is positive, which fits values that double. Very small n (below about 5) makes every one of these estimates unstable.

**Options:** There is no dialog. The analysis runs on every column of the data table at once, with a 95% CI. Results for all columns are laid side by side. In Python, `column_statistics(values, ci=0.9)` changes the level, and `hypothetical=μ₀` adds a one-sample t test and a Wilcoxon signed-rank test against μ₀.

**Checked against:** The documented spreadsheet examples for skewness, kurtosis and exclusive percentiles and quartiles.

**References:** Hyndman RJ, Fan Y (1996) Sample quantiles in statistical packages. *The American Statistician* 50:361–365. Zar JH, *Biostatistical Analysis*, chapters 4 and 6.

**Learn more:** [Statistics of central tendency (Handbook of Biological Statistics)](https://www.biostathandbook.com/central.html), [Standard deviation (Wikipedia)](https://en.wikipedia.org/wiki/Standard_deviation), [Skewness (Wikipedia)](https://en.wikipedia.org/wiki/Skewness).

**Related:** [[Mean]], [[Median]], [[Standard deviation]], [[Standard error]], [[Confidence interval]], [[Geometric mean]], [[Percentiles and quartiles]], [[Normality and lognormality tests]]

#### Normality and lognormality tests

<!-- opens: normality -->
<!-- equations: Normality tests -->

**In short:** Asks whether the values in a column could plausibly have come from a Gaussian (bell-shaped) distribution. Lognormality asks the same of the logarithms.

**When to use it:** To help you choose between a t test or ANOVA (which assume roughly Gaussian values) and a rank test. Do not rely on it alone: with few values every test passes almost always, and with very many values it fails for departures too small to matter. Look at the graph, and use what you know about how the measurement behaves. If the values are skewed but their logs are Gaussian, use the lognormality result and the [[Unpaired t test on logs]].

**What is done to your data:** Each column is tested on its own, and blank cells are left out. The Gaussian and the lognormal row groups are both shown for every column. For lognormality, every value must be greater than 0 and the tests run on ln(x); the base of the logarithm does not change any result. A test is not run (the sheet says "n too small") below its minimum n, or when all values are identical.

**How it's calculated:** Four tests are run. A small P is evidence against a Gaussian distribution. "Passed" means P > alpha (0.05).

| Test | Statistic | Minimum n |
|---|---|---|
| D'Agostino-Pearson omnibus | K² = Z(skewness)² + Z(kurtosis)², compared with a chi-square distribution with 2 df | 8 |
| Shapiro-Wilk | W = (Σ aᵢ x₍ᵢ₎)² / Σ(x − x̄)², where the weights aᵢ come from normal order statistics (Royston's algorithm, as in R's `shapiro.test`) | 3 |
| Anderson-Darling | A² = −n − (1/n) Σ (2i − 1)[ln Φ(zᵢ) + ln(1 − Φ(zₙ₊₁₋ᵢ))], with zᵢ = (x₍ᵢ₎ − x̄)/s. P comes from A²(1 + 0.75/n + 2.25/n²) | 8 |
| Kolmogorov-Smirnov | The largest gap between the data's cumulative distribution and a Gaussian with the sample mean and SD. P is the Lilliefors P by the Dallal-Wilkinson approximation (valid below 0.1; above 0.1 it comes from a simulated table) | 5 |

The sheet gives, for each test, the statistic, the P value, whether the column passed at alpha = 0.05, and the P value summary. The Anderson-Darling and Kolmogorov-Smirnov tests need the optional statsmodels package; without it only the first two are run, and the sheet says so.

**Worked example:** The smallest column Shapiro-Wilk accepts is three values, and there the test can be done by hand.

```example
A: 1, 2, 4
>>> normality(data["A"], tests=["shapiro"])
shapiro.statistic = 0.9643
shapiro.p = 0.6369
shapiro.passed = True
```

By hand:

- For n = 3 the weights are a = (−0.7071, 0, 0.7071). The numerator is (0.7071 · (4 − 1))² = 4.5.
- The mean is 7/3, and Σ(x − x̄)² = 1.778 + 0.111 + 2.778 = 4.667. So W = 4.5 / 4.667 = 0.9643.
- For n = 3 there is an exact P: P = (6/π) · (arcsin √W − arcsin √0.75) = (6/π) · (1.3807 − 1.0472) = 0.6369. In Python, `scipy.stats.shapiro([1, 2, 4])`; in R, `shapiro.test(c(1, 2, 4))`.
- P = 0.64 is above 0.05, so the column "passed". With three values this tells you almost nothing.

For larger n the weights come from a published algorithm, so check the other tests with `scipy.stats.shapiro`, `scipy.stats.normaltest` (D'Agostino-Pearson) and `statsmodels.stats.diagnostic.normal_ad` and `lilliefors`.

**Interpreting the results:** For each test, **Passed** = Yes means the data are consistent with a Gaussian distribution; it does not prove they are. **Passed** = No (P below alpha) means the values are unlikely to be Gaussian; the bigger n is, the less this matters in practice. If the tests disagree, trust the one that suits your n (Shapiro-Wilk is a good default for small samples). The minimum n is 8 for D'Agostino-Pearson and Anderson-Darling, so below that only Shapiro-Wilk (and, from 5, Kolmogorov-Smirnov) report a number. If the Gaussian row fails but the lognormal row passes, the data are better described as lognormal.

**Options:** There is no dialog. The analysis runs on every column, with alpha 0.05. In Python, `normality(values, alpha=0.01, tests=["shapiro"])` changes alpha and the tests, and `lognormality(values)` runs the log version.

**Checked against:** The Shapiro-Wilk (1965) worked example, and the published formulas of D'Agostino 1990, Anderson-Darling and Dallal-Wilkinson.

**References:** Shapiro SS, Wilk MB (1965) *Biometrika* 52:591–611. Royston P (1992) *Statistics and Computing* 2:117–119. D'Agostino RB, Belanger A, D'Agostino RB Jr (1990) *The American Statistician* 44:316–321. Anderson TW, Darling DA (1954) *J Am Stat Assoc* 49:765–769. Dallal GE, Wilkinson L (1986) *The American Statistician* 40:294–296.

**Learn more:** [Normality (Handbook of Biological Statistics)](https://www.biostathandbook.com/normality.html), [Shapiro-Wilk test (Wikipedia)](https://en.wikipedia.org/wiki/Shapiro%E2%80%93Wilk_test), [Log-normal distribution (Wikipedia)](https://en.wikipedia.org/wiki/Log-normal_distribution).

**Related:** [[Gaussian distribution]], [[Lognormal distribution]], [[P value]], [[Alpha]], [[Column statistics]], [[Unpaired t test]], [[Mann-Whitney test]]

#### Identify outliers (ROUT)

<!-- opens: outliers:rout, outliers -->
<!-- equations: Identifying outliers -->

**In short:** Finds one or several values that sit too far from the bulk of a column, using a robust fit that the outliers themselves cannot drag along. You choose how many false outliers you will tolerate (Q).

**When to use it:** To check columns for values that look wrong, especially when there may be more than one outlier. ROUT is the recommended method. Remove a value only for a reason you can report (for example a failed biological replicate), never because it is inconvenient. A single suspicious point in a roughly Gaussian column can also be checked with [[Grubbs' outlier test]].

**What is done to your data:** Each ticked column is tested separately and needs at least 3 values; blank cells are left out. The original table is never changed. Flagged values are shown shaded in the table, and a "cleaned" copy of the table can be made in which those cells are blank, so paired rows stay lined up. Only a constant (a robust mean) is fitted; no curve.

**How it's calculated:** This follows Motulsky and Brown (2006) for a column of N values:

- *Robust fit.* Start with μ at the median. Compute the robust SD of the residuals, RSDR = (68.27th percentile of |x − μ|) · N / (N − 1), with the percentile linearly interpolated. Then find the μ that minimizes the Lorentzian (Cauchy) merit function Σ ln(1 + ((x − μ)/RSDR)²). Repeat, updating RSDR and μ in turn, until they stop changing.
- *A t and P for each value.* t = |x − μ| / RSDR, and a two-tailed [[P value]] from the t distribution with N − 1 df.
- *Controlling the false discovery rate.* Sort the P values from smallest to largest. With Q the false discovery rate you chose, the j most extreme values are outliers for the largest j with P₍ⱼ₎ ≤ j · Q / N (the Benjamini-Hochberg rule; see [[False discovery rate (q values)]]).
- *At most 30%.* j can be at most 30% of N, rounded down, but never less than 1. So a column of 3 can have one outlier.

If more than 68% of the values are identical, RSDR is 0 and nothing is flagged; the sheet says so.

**Worked example:** Eight ordinary values and one far above the rest.

```example
A: 5, 6, 6, 7, 7, 8, 8, 9, 20
>>> identify_outliers(table, "rout")
n_outliers = 1
outliers.A[0].value = 20
details.A.centre = 7.065
details.A.rsdr = 1.650
details.A.p[8] = 5.05e-05
```

By hand:

- The robust fit gives μ = 7.065, close to the median 7 and not pulled by the 20. You can check this: the merit function Σ ln(1 + ((x − μ)/RSDR)²) is 7.1990 at 7.065, and larger at 6.965 and 7.165.
- The absolute residuals |x − μ|, sorted, are 0.065, 0.065, 0.935, 0.935, 1.065, 1.065, 1.935, 2.065, 12.935. The 68.27th percentile sits at position 8 · 0.6827 = 5.46 (counting from 0), between 1.065 and 1.935: 1.065 + 0.46 · 0.871 = 1.467. Then RSDR = 1.467 · 9 / 8 = 1.650.
- For the value 20: t = 12.935 / 1.650 = 7.840, and P = 2 · P(T₈ > 7.840) = 5.05 × 10⁻⁵ (Python `2 * scipy.stats.t.sf(7.84, 8)`; R `2 * pt(7.84, 8, lower.tail = FALSE)`).
- With Q = 1% and N = 9, the first sorted P must be at most 1 · 0.01 / 9 = 0.00111. It is 5.05 × 10⁻⁵, so 20 is an outlier. The next smallest P is 0.246, above 2 · 0.01 / 9 = 0.00222, so nothing else is flagged. The limit of 30% of 9 values (2) is not reached.

**Interpreting the results:** For each column the sheet gives the **number of values**, the **number of outliers**, the outlier values with their row numbers, the **robust centre** (μ) and the **RSDR**. Q is the share of flagged values you are willing to have wrongly flagged; a smaller Q is stricter. With no real outliers, Q behaves like alpha: about Q of the columns would still have one value flagged by chance. A flagged value is a candidate for investigation, not proof of an error. If you remove it, say so in the methods.

**Options:** The method (ROUT is recommended), Q (default 1%, 0.1% to 25%), and which columns to test. The "Total number of outliers" is summed over the columns.

**Checked against:** The false-positive rate of ROUT was checked by simulation. This implementation follows the paper's description, not any particular package's code. The percentile is linearly interpolated, and details of the robust fit may differ between implementations, so a borderline value can be flagged by one and not another.

**References:** Motulsky HJ, Brown RE (2006) Detecting outliers when fitting data with nonlinear regression. *BMC Bioinformatics* 7:123.

**Learn more:** [Outlier (Wikipedia)](https://en.wikipedia.org/wiki/Outlier).

**Related:** [[Outlier]], [[Grubbs' outlier test]], [[False discovery rate (q values)]], [[Column statistics]], [[Median]]

#### Grubbs' outlier test

<!-- opens: outliers:grubbs, outliers:iterative_grubbs -->
<!-- equations: Identifying outliers -->

**In short:** Tests whether the single most extreme value in a column is an outlier, assuming the rest of the values are Gaussian. The iterative version repeats the test after removing each outlier.

**When to use it:** When you expect at most one outlier in a column that is otherwise roughly Gaussian. If there may be two or more, use [[Identify outliers (ROUT)]]: two similar outliers can hide each other from Grubbs' test (masking), so that neither is found. Remove a value only for a reason you can report.

**What is done to your data:** Each ticked column is tested separately and needs at least 3 values; blank cells are left out. The original table is not changed. The cleaned copy has the flagged cells blank. The test is two-sided: it looks at the largest deviation in either direction.

**How it's calculated:** For n values with mean x̄ and SD s (denominator n − 1):

- G = max |x − x̄| / s, using the value farthest from the mean.
- Critical value at alpha: G_crit = ((n − 1) / √n) · √(t² / (n − 2 + t²)), where t is the upper α/(2n) point of the t distribution with n − 2 df.
- The value is an outlier when G > G_crit.
- The reported [[P value]] is the matching bound P = min(1, 2n · P(T₍n−2₎ > t_G)), with t_G = √(n(n − 2)G² / ((n − 1)² − nG²)). It is approximate (a Bonferroni-type bound) and is only given for the first round.
- *Iterative Grubbs'* removes the outlier and repeats on what is left, down to 3 values, until no value is flagged. Plain Grubbs' stops after the first round and finds at most one outlier.

**Worked example:**

```example
A: 5, 6, 6, 7, 7, 8, 8, 9, 20
>>> identify_outliers(table, "grubbs")
n_outliers = 1
outliers.A[0].value = 20
details.A.rounds[0].g = 2.566
details.A.rounds[0].critical = 2.215
details.A.rounds[0].p = 0.0002970
```

By hand:

- n = 9, mean = 76 / 9 = 8.444 and s = 4.503. The farthest value is 20, so G = (20 − 8.444) / 4.503 = 2.566.
- t is the upper 0.05 / 18 = 0.002778 point of t with 7 df: 3.947 (Python `scipy.stats.t.isf(0.05 / 18, 7)`; R `qt(1 - 0.05 / 18, 7)`).
- G_crit = (8 / 3) · √(3.947² / (7 + 3.947²)) = 2.667 · 0.8306 = 2.215. Since 2.566 > 2.215, the value 20 is an outlier.
- t_G = √(9 · 7 · 2.566² / (64 − 9 · 2.566²)) = √(414.8 / 4.734) = 9.361 (using the unrounded G). P = 2 · 9 · P(T₇ > 9.361) = 0.000297.

**Interpreting the results:** The sheet lists the outliers with their rows, **G** for the most extreme value, the **critical G** and the **approximate P**. G above the critical G (equivalently P below alpha) means that value is an outlier at alpha (default 0.05). If it is not, no value is flagged. The method assumes the other values are Gaussian; a skewed column will have a high value flagged for being in the long tail. The sheet warns about masking when you use the iterative form.

**Options:** The method (Grubbs' test or Iterative Grubbs' test), alpha (default 0.05; 0.001 to 0.2), and which columns to test.

**Checked against:** Grubbs' (1969) two-sided 5% critical values, and a simulation of the false-positive rate.

**References:** Grubbs FE (1969) Procedures for detecting outlying observations in samples. *Technometrics* 11:1–21.

**Learn more:** [Grubbs' test for outliers (NIST e-Handbook)](https://www.itl.nist.gov/div898/handbook/eda/section3/eda35h.htm), [Grubbs's test (Wikipedia)](https://en.wikipedia.org/wiki/Grubbs%27s_test).

**Related:** [[Outlier]], [[Identify outliers (ROUT)]], [[Standard deviation]], [[Gaussian distribution]], [[Column statistics]]

### Comparing two columns

#### Unpaired t test

<!-- opens: compare:unpaired_t, compare -->
<!-- equations: t tests -->

**In short:** Asks whether two independent groups have different means, assuming both come from Gaussian distributions with the same SD.

**When to use it:** Two groups of different subjects (for example control and treated biological replicates), with values that are roughly Gaussian and SDs that are similar. If the SDs may differ, use [[Welch's t test]]. If the same subjects are measured twice, use [[Paired t test]]. If the values are far from Gaussian and you cannot transform them, use [[Mann-Whitney test]].

**What is done to your data:** Blank cells are left out, column by column. Every remaining value counts as one independent observation, so each value should be one biological replicate (see [[N and n]]). Nothing else is changed: no outliers are removed and no values are transformed.

**How it's calculated:** With A the first column and B the second:

- The mean and SD of each column: x̄_A, s_A, x̄_B, s_B, with n_A and n_B values.
- The pooled variance, the average of the two variances weighted by their [[Degrees of freedom|df]]:

      s_p² = ((n_A − 1)s_A² + (n_B − 1)s_B²) / (n_A + n_B − 2)

- The [[Standard error|standard error]] of the difference, SE = s_p · √(1/n_A + 1/n_B).
- t = (x̄_B − x̄_A) / SE, with df = n_A + n_B − 2.
- The two-tailed [[P value]] is the chance that a t distribution with that df lies further from 0 than ±t: P = 2 · P(T > |t|).
- The 95% [[Confidence interval|confidence interval]] of the difference is (x̄_B − x̄_A) ± t* · SE, where t* is the 97.5th percentile of the t distribution with df degrees of freedom.
- R² = t² / (t² + df), and [[Effect size|Cohen's d]] = (x̄_B − x̄_A) / s_p.
- The F test of equal variances: F = larger s² / smaller s², with (n − 1) df for each column.

**Worked example:** Three biological replicates per group.

```example
Control: 3, 5, 7
Treated: 9, 11, 13
>>> compare(table, "unpaired_t")
mean_a = 5
mean_b = 11
sd_a = 2
sd_b = 2
difference = 6
se = 1.633
t = 3.674
df = 4
p = 0.02131
ci_low = 1.466
ci_high = 10.53
cohen_d = 3
```

By hand:

- Control: mean 5, deviations −2, 0, 2, so s² = (4 + 0 + 4)/2 = 4 and s = 2. Treated: mean 11, s = 2.
- s_p² = (2·4 + 2·4) / 4 = 4, so s_p = 2.
- SE = 2 · √(1/3 + 1/3) = 2 · 0.8165 = 1.633.
- t = (11 − 5) / 1.633 = 3.674, with df = 3 + 3 − 2 = 4.
- P = 2 · P(T₄ > 3.674) = 0.0213. In Python, `2 * scipy.stats.t.sf(3.674, 4)`; in R, `2 * pt(3.674, 4, lower.tail = FALSE)`.
- t* for 95% and 4 df is 2.776, so the CI is 6 ± 2.776 · 1.633 = 1.466 to 10.53.

**Interpreting the results:** The **P value** is the chance of a difference at least this large if the two groups really had the same mean. A small P (below alpha, usually 0.05) is evidence that the means differ; a large P means the data do not show a difference, not that there is none. The **difference and its confidence interval** say how big the effect is: with 95% confidence the true difference lies in the interval, and an interval that excludes 0 goes with P < 0.05. **R²** is the fraction of all the variation explained by the group. A small P in the **F test** means the SDs differ; consider [[Welch's t test]].

**Options:** One- or two-tailed P (choose one-tailed only if you predicted the direction before seeing the data), alpha, the confidence level, and the P value style. In Advanced, the df and the hypothesised difference (the difference tested instead of 0) can be set by hand; the sheet then lists them under "Changed from the default".

**Checked against:** R `t.test(var.equal = TRUE)` and `var.test` on the `sleep` data.

**References:** Student (1908) *Biometrika* 6:1–25. Zar JH, *Biostatistical Analysis*, chapter 8.

**Learn more:** [Student's t test (Handbook of Biological Statistics)](https://www.biostathandbook.com/twosamplettest.html), [t tests (NIST e-Handbook)](https://www.itl.nist.gov/div898/handbook/eda/section3/eda353.htm), [StatQuest video index](https://statquest.org/video-index/).

**Related:** [[P value]], [[Confidence interval]], [[Standard error]], [[Effect size]], [[Welch's t test]], [[Mann-Whitney test]]

#### Welch's t test

<!-- opens: compare:welch_t -->
<!-- equations: t tests -->

**In short:** Asks whether two independent groups have different means, without assuming that both groups have the same SD. It is the recommended default for two independent Gaussian groups.

**When to use it:** Two groups of different subjects, with values that are roughly Gaussian. Use it when the SDs, or the numbers of values, differ between groups, and as the safe choice when you are not sure. When the SDs really are equal it loses almost nothing against the [[Unpaired t test]]. For matched subjects use the [[Paired t test]]; for values far from Gaussian use the [[Mann-Whitney test]].

**What is done to your data:** Blank cells are left out, column by column, and each column needs at least 2 values. Every remaining value is one independent observation, so each should be one biological replicate (see [[N and n]]). Nothing is removed or transformed.

**How it's calculated:** With A the first column and B the second:

- The mean, SD and n of each column.
- The standard error of the difference does not pool the variances:

      SE = √(s_A²/n_A + s_B²/n_B)

- t = (x̄_B − x̄_A) / SE.
- The [[Degrees of freedom|df]] come from the Welch-Satterthwaite equation, and are usually not a whole number:

      df = SE⁴ / ( (s_A²/n_A)² / (n_A − 1) + (s_B²/n_B)² / (n_B − 1) )

- The two-tailed [[P value]] is 2 · P(T > |t|) for a t distribution with that df. It is computed exactly from that distribution.
- The 95% [[Confidence interval|confidence interval]] of the difference is (x̄_B − x̄_A) ± t* · SE, with t* the 97.5th percentile for that df.
- R² = t² / (t² + df). [[Effect size|Cohen's d, Hedges' g and Glass's delta]] are also given; d and g use the pooled SD even though the test does not.

**Worked example:** Three control values and four treated values with a larger spread.

```example
Control: 3, 5, 7
Treated: 2, 6, 10, 14
>>> compare(table, "welch_t")
mean_a = 5
mean_b = 8
sd_a = 2
sd_b = 5.164
difference = 3
se = 2.828
df = 4.075
t = 1.061
p = 0.3476
ci_low = -4.796
ci_high = 10.80
```

By hand:

- Control: mean 5, deviations −2, 0, 2, so s² = 8/2 = 4. Treated: mean 8, deviations −6, −2, 2, 6, so s² = 80/3 = 26.67 and s = 5.164.
- s_A²/n_A = 4/3 = 1.333 and s_B²/n_B = 26.67/4 = 6.667. SE = √8 = 2.828.
- t = (8 − 5) / 2.828 = 1.061.
- df = 8² / (1.333²/2 + 6.667²/3) = 64 / (0.889 + 14.815) = 4.075.
- P = 2 · P(T₄.₀₇₅ > 1.061) = 0.3476. In Python, `2 * scipy.stats.t.sf(1.0607, 4.0755)`; in R, `2 * pt(1.0607, 4.0755, lower.tail = FALSE)`.
- t* for 4.075 df is 2.756, so the CI is 3 ± 2.756 · 2.828 = −4.796 to 10.80.

**Interpreting the results:** The **P value** is the chance of a difference in means at least this large if the two groups really had the same mean. Here P is large, so these data do not show a difference (with so few values they could not). The **difference and its confidence interval** say how large the effect could be; an interval that includes 0 goes with P above 0.05. The **df** are smaller than n_A + n_B − 2 (here 4.075 against 5) because the unequal SDs cost information. **R²** is the fraction of the variation explained by the group.

**Options:** One- or two-tailed P (one-tailed only if you predicted the direction before seeing the data), alpha, the confidence level and the P value style. In Advanced, the df (or the conservative choice, the smaller group's N − 1) and the hypothesised difference can be set by hand; the sheet then lists them under "Changed from the default".

**Checked against:** R `t.test` (Welch) on the `sleep` and `mtcars` data.

**References:** Welch BL (1947) *Biometrika* 34:28–35. Satterthwaite FE (1946) *Biometrics Bulletin* 2:110–114.

**Learn more:** [Welch's t test (Wikipedia)](https://en.wikipedia.org/wiki/Welch%27s_t-test), [Student's t test (Handbook of Biological Statistics)](https://www.biostathandbook.com/twosamplettest.html).

**Related:** [[Unpaired t test]], [[Degrees of freedom]], [[P value]], [[Confidence interval]], [[Standard error]], [[Mann-Whitney test]]

#### Unpaired t test on logs

<!-- opens: compare:lognormal_t -->
<!-- equations: t tests -->

**In short:** Asks whether two independent groups differ when the comparison is made on the log scale. It tests the ratio of the geometric means, not the difference of the means.

**When to use it:** Two independent groups of positive values that are roughly lognormal: skewed to the right, with the spread growing with the mean, as for many concentrations, intensities and fold changes. If the logs look Gaussian (see [[Normality and lognormality tests]]), this test is more appropriate than the [[Unpaired t test]] on the raw values. For matched subjects use the [[Ratio paired t test]].

**What is done to your data:** Blank cells are left out, column by column. Every value must be greater than 0, otherwise the test is refused. All values are replaced by their base 10 logarithms, and the pooled-variance t test is run on those logs. The raw values are not changed. Each value should be one biological replicate (see [[N and n]]).

**How it's calculated:** Let a = log₁₀ of each A value and b = log₁₀ of each B value.

- The mean and SD of the logs in each column, and the pooled variance s_p² = ((n_A − 1)s_a² + (n_B − 1)s_b²) / (n_A + n_B − 2).
- SE = s_p · √(1/n_A + 1/n_B), t = (mean b − mean a) / SE, df = n_A + n_B − 2, and the two-tailed [[P value]] from the t distribution.
- The 95% CI of the difference of logs is (mean b − mean a) ± t* · SE.
- Back on the original scale: the [[Geometric mean|geometric means]] are 10^(mean a) and 10^(mean b). The **ratio of geometric means** B/A is 10^(mean b − mean a), and its CI is 10 raised to the ends of the CI of the difference of logs.
- An F test compares the variances of the logs, as for the unpaired t test.

Choosing log₁₀ rather than ln changes nothing in t, df, P or the ratio.

**Worked example:** Values spread over two orders of magnitude, so the logs are 0, 1, 2 and 1, 2, 3.

```example
Control: 1, 10, 100
Treated: 10, 100, 1000
>>> compare(table, "lognormal_t")
geomean_a = 10
geomean_b = 100
log_difference = 1
log_se = 0.8165
t = 1.225
df = 4
p = 0.2879
ratio = 10
ratio_ci_low = 0.05408
ratio_ci_high = 1849
```

By hand:

- Logs of Control: 0, 1, 2, mean 1, SD 1. Logs of Treated: 1, 2, 3, mean 2, SD 1. The geometric means are 10¹ = 10 and 10² = 100.
- s_p = 1, SE = 1 · √(1/3 + 1/3) = 0.8165. The difference of the logs is 1, so t = 1 / 0.8165 = 1.225, with df = 4.
- P = 2 · P(T₄ > 1.225) = 0.2879. In Python, `2 * scipy.stats.t.sf(1.2247, 4)`; in R, `2 * pt(1.2247, 4, lower.tail = FALSE)`.
- The ratio of geometric means is 10^1 = 10. With t* = 2.776, the CI of the log difference is 1 ± 2.776 · 0.8165 = −1.267 to 3.267, so the CI of the ratio is 10^−1.267 to 10^3.267 = 0.0541 to 1849.

**Interpreting the results:** The **P value** says whether the ratio of geometric means differs from 1. The **ratio and its confidence interval** are the effect size: a ratio of 10 means the typical Treated value is ten times the typical Control value. A CI that includes 1 goes with P above 0.05, as here, where three values per group cannot pin the ratio down (the interval runs from 0.05 to 1849). Do not read the geometric means as arithmetic means. The F test of the variances of the logs is a check that the spread is similar on the log scale.

**Options:** One- or two-tailed P, alpha, the confidence level and the P value style. In Advanced, the df can be set by hand (or the conservative choice, the smaller group's N − 1). The dialog uses the pooled-variance version; in Python, `lognormal_unpaired_t(a, b, welch=True)` gives the Welch version on logs.

**Checked against:** The t test on logs applies the checked unpaired t test (R `t.test(var.equal = TRUE)`) to log₁₀ values.

**References:** Zar JH, *Biostatistical Analysis*, chapter 8 (data transformations).

**Learn more:** [Data transformations (Handbook of Biological Statistics)](https://www.biostathandbook.com/transformation.html), [Log-normal distribution (Wikipedia)](https://en.wikipedia.org/wiki/Log-normal_distribution).

**Related:** [[Lognormal distribution]], [[Geometric mean]], [[Unpaired t test]], [[Welch's t test]], [[Ratio paired t test]], [[Normality and lognormality tests]]

#### Paired t test

<!-- opens: compare:paired_t -->
<!-- equations: t tests -->

**In short:** Asks whether the average change within matched pairs differs from zero, assuming the differences are Gaussian.

**When to use it:** The same subject measured twice (before and after, or two treatments on the same sample), or naturally matched samples, with differences that are roughly Gaussian. Pairing removes the variation between subjects, so it is more powerful than the [[Unpaired t test]] when the pairs go together. If the change is better described as a ratio, use the [[Ratio paired t test]]; if the differences are far from Gaussian, use the [[Wilcoxon matched-pairs test]].

**What is done to your data:** Row i of column A is paired with row i of column B, so the rows must line up (the table should be marked Paired; otherwise the sheet warns you). A row with a blank in either column is left out, and the sheet reports how many were dropped. The test works on the differences d = B − A. At least 2 complete pairs are needed. Each pair should be one biological replicate (see [[Paired data]] and [[N and n]]).

**How it's calculated:**

- Differences dᵢ = Bᵢ − Aᵢ, with n pairs, mean d̄ and SD s_d.
- SE = s_d / √n, t = d̄ / SE, df = n − 1.
- The two-tailed [[P value]] is 2 · P(T > |t|) from the t distribution.
- The 95% [[Confidence interval|confidence interval]] of the mean difference is d̄ ± t* · SE.
- R² = t² / (t² + df). The paired effect sizes are Cohen's d_z = d̄ / s_d and Hedges' g_z = J(n − 1) · d_z.
- *How effective was the pairing?* The Pearson correlation r between A and B, and a one-tailed P for r > 0 from t = r · √(n − 2) / √(1 − r²) with n − 2 df. A significant r means the pairing helped.

**Worked example:** Four subjects measured before and after.

```example
Before: 10, 12, 14, 16
After: 12, 15, 15, 20
>>> table = ColumnTable.from_columns(data, paired=True)
>>> compare(table, "paired_t")
n = 4
difference = 2.5
sd_difference = 1.291
se = 0.6455
t = 3.873
df = 3
p = 0.03047
ci_low = 0.4457
ci_high = 4.554
cohen_dz = 1.936
pairing_r = 0.9342
pairing_p = 0.03290
```

By hand:

- The differences After − Before are 2, 3, 1, 4. Their mean is 2.5, the deviations are −0.5, 0.5, −1.5, 1.5, and s_d² = (0.25 + 0.25 + 2.25 + 2.25)/3 = 1.667, so s_d = 1.291.
- SE = 1.291 / √4 = 0.6455, t = 2.5 / 0.6455 = 3.873, df = 3.
- P = 2 · P(T₃ > 3.873) = 0.0305. In Python, `2 * scipy.stats.t.sf(3.873, 3)`; in R, `2 * pt(3.873, 3, lower.tail = FALSE)`.
- t* for 3 df is 3.182, so the CI is 2.5 ± 3.182 · 0.6455 = 0.4457 to 4.554.
- d_z = 2.5 / 1.291 = 1.936 and R² = 3.873² / (3.873² + 3) = 0.8333.
- The columns have SSA = 20, SSB = 33 and a cross-product of 24, so r = 24 / √(20 · 33) = 0.9342. Then t = 0.9342 · √2 / √(1 − 0.9342²) = 3.703 with 2 df, and the one-tailed P is 0.0329.

**Interpreting the results:** The **P value** is the chance of an average change at least this large if the true average change were zero. Here P < 0.05, so the change is unlikely to be noise. The **mean difference and its CI** give the size of the change (After − Before) in your units. **R²** is the fraction of the variation in the differences that is the average change. The **pairing** rows tell you whether matching helped: a significant r means yes; a small r means the pairing bought little and an unpaired design would have been nearly as good (but you cannot change the test after the fact because of this).

**Options:** One- or two-tailed P, alpha, the confidence level and the P value style. In Advanced, the df and the hypothesised mean difference (tested instead of 0) can be set by hand; the sheet then lists them under "Changed from the default". The conservative df option is the same as n − 1 here.

**Checked against:** R `t.test(paired = TRUE)` on the `sleep` data.

**References:** Student (1908) *Biometrika* 6:1–25. Zar JH, *Biostatistical Analysis*, chapter 9.

**Learn more:** [Paired t test (Handbook of Biological Statistics)](https://www.biostathandbook.com/pairedttest.html), [Student's t test (Wikipedia)](https://en.wikipedia.org/wiki/Student%27s_t-test).

**Related:** [[Paired data]], [[Ratio paired t test]], [[Wilcoxon matched-pairs test]], [[Unpaired t test]], [[P value]], [[Confidence interval]]

#### Ratio paired t test

<!-- opens: compare:ratio_paired_t -->
<!-- equations: t tests -->

**In short:** Asks whether the typical ratio After/Before within matched pairs differs from 1. It is a paired t test on the logs of the values.

**When to use it:** Matched measurements where the change is multiplicative: each subject changes by a similar factor (about twofold, say) rather than by a similar amount, so the difference between members of a pair grows with the size of the values. Typical for normalized intensities and fold changes. If the changes are additive, use the [[Paired t test]]; if the differences are far from Gaussian, use the [[Wilcoxon matched-pairs test]].

**What is done to your data:** Row i of column A is paired with row i of column B; rows with a blank in either column are left out. Every value must be greater than 0, otherwise the test is refused. At least 2 complete pairs are needed. Both columns are converted to log₁₀, and the paired t test is run on log₁₀(B) − log₁₀(A) = log₁₀(B/A). Each pair should be one biological replicate.

**How it's calculated:**

- For each pair, the log ratio dᵢ = log₁₀(Bᵢ) − log₁₀(Aᵢ). Then n, the mean d̄, and SD s_d of the d.
- SE = s_d / √n, t = d̄ / SE, df = n − 1, and the two-tailed [[P value]] from the t distribution.
- The 95% CI of the mean log ratio is d̄ ± t* · SE.
- The **geometric mean of the ratios** B/A is 10^d̄, and its CI is 10 raised to the ends of the CI of d̄. A CI that excludes 1 goes with P < 0.05.
- The pairing check (Pearson r between the logs, one-tailed P) is as for the paired t test.

The base of the logarithm does not change t, df, P or the ratio.

**Worked example:** Four pairs whose ratios After/Before are 2, 2.5, 1.5 and 3.

```example
Before: 10, 20, 40, 50
After: 20, 50, 60, 150
>>> table = ColumnTable.from_columns(data, paired=True)
>>> compare(table, "ratio_paired_t")
log_difference = 0.3380
log_sd_difference = 0.1298
log_se = 0.06489
t = 5.210
df = 3
p = 0.01375
ratio = 2.178
ratio_ci_low = 1.354
ratio_ci_high = 3.504
```

By hand:

- The log₁₀ ratios are log₁₀ 2 = 0.3010, log₁₀ 2.5 = 0.3979, log₁₀ 1.5 = 0.1761 and log₁₀ 3 = 0.4771. Their mean is 0.3380 and their SD is 0.1298.
- SE = 0.1298 / √4 = 0.06489, t = 0.3380 / 0.06489 = 5.210, df = 3.
- P = 2 · P(T₃ > 5.210) = 0.01375. In Python, `2 * scipy.stats.t.sf(5.2095, 3)`; in R, `2 * pt(5.2095, 3, lower.tail = FALSE)`.
- The geometric mean ratio is 10^0.3380 = 2.178. With t* = 3.182, the CI of the mean log ratio is 0.3380 ± 3.182 · 0.06489 = 0.1315 to 0.5446, so the CI of the ratio is 10^0.1315 to 10^0.5446 = 1.354 to 3.504.

**Interpreting the results:** The **geometric mean ratio and its CI** are the effect size: 2.18 means that, typically, After is about 2.2 times Before, and the true ratio is likely between 1.35 and 3.50. The **P value** tests whether that ratio differs from 1. It is not the ordinary mean of the four ratios (2.25). The sheet also gives the mean log difference and its CI on the log₁₀ scale, **R²**, and the pairing check.

**Options:** One- or two-tailed P, alpha, the confidence level and the P value style. In Advanced, the df can be set by hand.

**Checked against:** The ratio test applies the checked paired t test (R `t.test(paired = TRUE)`) to log₁₀ values.

**References:** Zar JH, *Biostatistical Analysis*, chapter 9.

**Learn more:** [Geometric mean (Wikipedia)](https://en.wikipedia.org/wiki/Geometric_mean), [Paired t test (Handbook of Biological Statistics)](https://www.biostathandbook.com/pairedttest.html).

**Related:** [[Paired t test]], [[Unpaired t test on logs]], [[Geometric mean]], [[Paired data]], [[Lognormal distribution]]

#### Mann-Whitney test

<!-- opens: compare:mann_whitney -->
<!-- equations: Rank tests -->

**In short:** Asks whether values in one group tend to be larger than values in the other, using only the ranks of the values. It does not assume a Gaussian distribution.

**When to use it:** Two independent groups where the values are far from Gaussian and cannot be transformed, or are ordinal scores, or contain extreme values. It answers a slightly different question than a t test: whether one group tends to give larger values, which is a statement about medians if the two distributions have the same shape. With very few values it has little power; see the warning below. Prefer the [[Welch's t test]] when the values are roughly Gaussian.

**What is done to your data:** Blank cells are left out, column by column, and each column needs at least 1 value. All values from both columns are put together and ranked from smallest (rank 1); tied values share the average of their ranks. The actual values are then used only for the medians and the Hodges-Lehmann difference. Each value should be one biological replicate (see [[Ranks and ties]] and [[N and n]]).

**How it's calculated:** With m values in A and n in B:

- Rank all N = m + n values together. R_A and R_B are the sums of the ranks in each column.
- U_B = R_B − n(n + 1)/2 is the number of (A, B) pairs in which B is larger (a tie counts one half). U_A = m · n − U_B. The sheet reports U = min(U_A, U_B).
- The two-tailed [[P value]] is **exact** (ties included) when the smaller column has at most 100 values and m · n ≤ 20 000. It is found by counting every way the observed ranks could be split between the two groups, and doubling the smaller tail (at most 1). Otherwise it comes from the Gaussian approximation z = (U_B − mn/2) / σ with σ² = (mn/12)[(N + 1) − Σ(t³ − t) / (N(N − 1))], where t is the size of each group of ties. There is no continuity correction by default. The sheet says whether P is exact or approximate.
- The **Hodges-Lehmann** difference is the median of all m · n differences Bⱼ − Aᵢ. Its confidence interval runs between two of the sorted differences. Only some confidence levels are reachable, so the interval has the achievable level nearest to the one you asked for, and the sheet prints the level it reached (for example 94.29%) with a warning.
- The medians of the two columns and their difference are also given.

*The smallest possible P.* With an exact P, the smallest value any data can give is 2 / C(m + n, m). At 3 vs 3 it is 2/20 = 0.10, so the test can never reach P < 0.05; at 4 vs 4 it is 2/70 = 0.029. The sheet warns when this floor is above alpha.

**Worked example:** Four values per group, no ties.

```example
Control: 2, 4, 6, 9
Treated: 5, 8, 11, 13
>>> compare(table, "mann_whitney")
rank_sum_a = 13
rank_sum_b = 23
u = 3
p = 0.2000
median_a = 5
median_b = 9.5
hodges_lehmann = 4
ci_low = -1
ci_high = 9
ci_achieved = 0.9429
```

By hand:

- Sorted together: 2 (Control), 4 (C), 5 (Treated), 6 (C), 8 (T), 9 (C), 11 (T), 13 (T), with ranks 1 to 8. R_Control = 1 + 2 + 4 + 6 = 13 and R_Treated = 3 + 5 + 7 + 8 = 23 (they add to 8 · 9 / 2 = 36).
- U_Treated = 23 − 4 · 5 / 2 = 13 and U_Control = 16 − 13 = 3, so U = 3.
- Exact P: there are C(8, 4) = 70 equally likely ways to split the ranks. The number with U ≤ 3 is 1 + 1 + 2 + 3 = 7 (the partitions of 0, 1, 2 and 3 into at most four parts of size at most four). So P = 2 · 7/70 = 0.2. In Python, `scipy.stats.mannwhitneyu(treated, control, method="exact")`; in R, `wilcox.test(treated, control)`.
- Hodges-Lehmann: the 16 differences Treated − Control sorted are −4, −1, −1, 1, 2, 2, 3, 4, 4, 5, 6, 7, 7, 9, 9, 11. The median is the average of the 8th and 9th, (4 + 4)/2 = 4. For the CI, k = 2 gives confidence 1 − 2 · P(U ≤ 1) = 1 − 2 · 2/70 = 94.29%, so the CI is the 2nd smallest to the 2nd largest difference, −1 to 9.

**Interpreting the results:** The **P value** is the chance of ranks this lopsided (or more) if the two groups came from the same distribution. Here it is 0.20, so the data do not show a difference. The **rank sums** show which group has the larger values (the larger sum). **U** is the smaller of the two counts of (Control, Treated) pairs in which one group's value is the larger; U near 0 means almost complete separation. The **medians** describe each group, and the **Hodges-Lehmann difference** with its CI is the effect size. A rank test is not more sensitive than a t test with Gaussian data; its advantage is that it is not misled by outliers or skew.

**Options:** One- or two-tailed P (one-tailed only if you predicted the direction), alpha, the confidence level and the P value style. In Python, `exact=True` or `False` forces the P method and `continuity=True` adds R's 0.5.

**Checked against:** R `wilcox.test` on `mtcars` (unequal n and ties) and the exact examples of Hollander and Wolfe; brute-force permutation counts of the exact P values. How other packages build the exact two-tailed P and the CI with ties is not yet checked: this engine doubles the smaller tail and uses the order-statistic rule for untied data, and its Hodges-Lehmann difference is B − A.

**References:** Mann HB, Whitney DR (1947) *Ann Math Stat* 18:50–60. Hollander M, Wolfe DA, *Nonparametric Statistical Methods*. Sheskin DJ, *Handbook of Parametric and Nonparametric Statistical Procedures*.

**Learn more:** [Mann-Whitney U test (Wikipedia)](https://en.wikipedia.org/wiki/Mann%E2%80%93Whitney_U_test), [Kruskal-Wallis and Mann-Whitney (Handbook of Biological Statistics)](https://www.biostathandbook.com/kruskalwallis.html).

**Related:** [[Ranks and ties]], [[Exact and approximate P values]], [[Hodges-Lehmann estimate]], [[Welch's t test]], [[Kolmogorov-Smirnov test]], [[Kruskal-Wallis test]]

#### Kolmogorov-Smirnov test

<!-- opens: compare:kolmogorov_smirnov -->
<!-- equations: Rank tests -->

**In short:** Asks whether two independent groups come from the same distribution, by finding the biggest gap between their cumulative distributions. It is sensitive to any difference in shape, not only in the middle.

**When to use it:** Two independent groups when a difference in spread or shape matters as much as a difference in location. It is less powerful than the [[Mann-Whitney test]] for a plain shift of the middle, so use it when you care about the whole distribution. It is best with larger groups; with tied values its P is conservative.

**What is done to your data:** Blank cells are left out, column by column, and each column needs at least 1 value. The values themselves are used (the test depends only on their order). The medians are shown for reference. Each value should be one biological replicate (see [[N and n]]).

**How it's calculated:**

- For every value x, the cumulative fraction of A at or below x, F_A(x), and of B, F_B(x).
- D = maxₓ |F_A(x) − F_B(x)|, the largest vertical gap between the two step curves. D lies between 0 (identical) and 1 (completely separate).
- The two-tailed [[P value]] is the chance of a D at least this large if both groups come from the same continuous distribution. It is **exact** (computed with scipy) when n_A · n_B < 10 000, otherwise asymptotic. With tied values the exact P assumes continuous data and is conservative; the sheet warns when there are ties.
- For a one-tailed test predicting B larger, D⁺ = max(F_A − F_B) is used.
- There is no confidence interval, so the confidence level is not used.

**Worked example:** Four values per group that overlap a little.

```example
Control: 1, 2, 3, 4
Treated: 3.5, 5, 6, 7
>>> compare(table, "kolmogorov_smirnov")
d = 0.75
p = 0.2286
exact = True
median_a = 2.5
median_b = 5.5
```

By hand:

- Control values are at 1, 2, 3, 4, and Treated at 3.5, 5, 6, 7. At x = 3 the Control curve is at 3/4 and the Treated curve is still 0, a gap of 0.75. At x = 4 it is 1 − 1/4 = 0.75 again. Nowhere is the gap larger, so D = 0.75.
- Exact P: when the 8 values are in order, each is labelled A or B, and there are C(8, 4) = 70 equally likely orderings. D = 0.75 means that, walking through the order, the count of A's minus the count of B's reaches ±3 at some point. By the reflection principle, 8 orderings touch +3 and 8 touch −3, and none touch both, so P = 16/70 = 0.2286. In Python, `scipy.stats.ks_2samp(control, treated)`; in R, `ks.test(control, treated)`.

**Interpreting the results:** **D** is the biggest difference between the two cumulative distributions, so D = 0.75 means that, at some value, 75 percentage points more of one group lies below it than of the other. The **P value** says whether such a gap is surprising if the groups were the same. Small n makes P large even for a big D (4 vs 4 can reach 0.029 at best). A significant result tells you the distributions differ but not how; look at the graph and the **medians**.

**Options:** One- or two-tailed P, alpha and the P value style. The confidence level is not used. In Python, `exact=False` forces the asymptotic P.

**Checked against:** The exact P by brute-force permutation counting in the tests.

**References:** Smirnov NV (1948) *Ann Math Stat* 19:279–281. Hodges JL (1958) *Arkiv för Matematik* 3:469–486.

**Learn more:** [Kolmogorov-Smirnov test (Wikipedia)](https://en.wikipedia.org/wiki/Kolmogorov%E2%80%93Smirnov_test), [Kolmogorov-Smirnov goodness-of-fit test (NIST e-Handbook)](https://www.itl.nist.gov/div898/handbook/eda/section3/eda35g.htm).

**Related:** [[Mann-Whitney test]], [[Exact and approximate P values]], [[Ranks and ties]], [[Median]], [[P value]]

#### Wilcoxon matched-pairs test

<!-- opens: compare:wilcoxon -->
<!-- equations: Rank tests -->

**In short:** Asks whether the differences within matched pairs tend to be positive or negative, using only the ranks of the sizes of the differences. It does not assume Gaussian differences.

**When to use it:** The same subjects measured twice (or naturally matched pairs) when the differences are far from Gaussian or contain extreme values. It is the rank-based counterpart of the [[Paired t test]]. It needs enough pairs to be able to reach significance: 6 pairs is the least that can give P < 0.05 (2/2⁶ = 0.031).

**What is done to your data:** Row i of column A is paired with row i of column B; rows with a blank in either column are left out and counted. The differences d = B − A are calculated. Pairs with d = 0 are excluded before ranking (Wilcoxon's method, the default); the sheet reports how many there were. The sizes |d| are ranked from smallest (rank 1); ties share the average rank. Each pair should be one biological replicate (see [[Ranks and ties]] and [[Paired data]]).

**How it's calculated:**

- d = B − A for each pair; drop the zeros; rank |d|.
- Sum of positive ranks (differences with B larger), sum of negative ranks, and W = (sum of positive ranks) − (sum of negative ranks). This W is a signed difference, not the smaller rank sum that some books report.
- The two-tailed [[P value]] is **exact** (ties included) for fewer than 200 nonzero differences: each rank is given a + or − sign with probability 1/2, all 2ⁿ patterns are counted, and the smaller tail is doubled (at most 1). For larger n it comes from the Gaussian approximation with mean n(n + 1)/4 and variance n(n + 1)(2n + 1)/24 − Σ(t³ − t)/48, with no continuity correction by default.
- The median of the differences is reported, with the **Hodges-Lehmann** pseudo-median: the median of the Walsh averages (dᵢ + dⱼ)/2 over all i ≤ j. Its CI runs between two sorted Walsh averages, at the achievable level nearest to the one you asked for. The sheet prints the level reached and warns if it differs from the request.
- *How effective was the pairing?* Spearman's rₛ between the two columns and a one-tailed P.

*The smallest possible P.* With n nonzero differences, the exact two-tailed P cannot go below 2/2ⁿ: 5 pairs, 0.0625; 6 pairs, 0.031. The sheet warns when this is above alpha.

**Worked example:** Six pairs, where five of the six changes are increases.

```example
Before: 20, 22, 25, 30, 35, 40
After: 22, 21, 30, 40, 43, 44
>>> table = ColumnTable.from_columns(data, paired=True)
>>> compare(table, "wilcoxon")
sum_positive = 20
sum_negative = 1
w = 19
p = 0.0625
n_used = 6
min_p = 0.03125
hodges_lehmann = 4.5
ci_low = 0.5
ci_high = 9
ci_achieved = 0.9375
```

By hand:

- The differences After − Before are 2, −1, 5, 10, 8, 4. Their sizes in order are 1, 2, 4, 5, 8, 10, with ranks 1 to 6. So |−1| has rank 1, 2 has rank 2, 4 has rank 3, 5 has rank 4, 8 has rank 5 and 10 has rank 6.
- The positive ranks are 2 + 3 + 4 + 5 + 6 = 20 and the negative rank is 1. W = 20 − 1 = 19.
- Exact P: there are 2⁶ = 64 sign patterns. The observed negative-rank sum is 1. Only 2 of the 64 patterns give a negative-rank sum of 1 or less: none negative (sum 0), or only rank 1 negative (sum 1). So the smaller tail is 2/64 and P = 2 · 2/64 = 0.0625. The smallest P possible with 6 pairs is 2/64 = 0.031, which needs all differences to have one sign. In Python, `scipy.stats.wilcoxon(after, before)`; in R, `wilcox.test(after, before, paired = TRUE)`.
- Hodges-Lehmann: the 21 Walsh averages sorted run from −1 to 10, and the 11th (the median) is 4.5. The CI at 93.75% = 1 − 2 · P(T ≤ 1) = 1 − 4/64 uses the 2nd smallest and 2nd largest Walsh average: 0.5 and 9.

**Interpreting the results:** The **P value** is the chance of a pattern of signed ranks at least this lopsided if the true median difference were 0. Here one decrease against five increases gives 0.0625, just above 0.05: with 6 pairs even a single reversed pair is enough to lose significance. The **sums of positive and negative ranks** show which direction wins. The **median of the differences** and the **Hodges-Lehmann pseudo-median with its CI** are the effect size. The **pairing** rows show whether the matching was effective.

**Options:** One- or two-tailed P, alpha, the confidence level and the P value style. In Python, `zero_method="pratt"` ranks the zero differences and then leaves their ranks out of the sums (Pratt 1959) instead of dropping them, `exact=True` or `False` forces the P method, and `continuity=True` adds R's 0.5.

**Checked against:** R `wilcox.test(paired = TRUE)` on the `sleep` data, and the exact Wilcoxon examples of Hollander and Wolfe in R's `?wilcox.test`; brute-force permutation counts of the exact P values. Not yet checked against other packages: the default for zero differences (this engine excludes them), the way the exact two-tailed P is made (it doubles the smaller tail), and the CI when there are ties.

**References:** Wilcoxon F (1945) *Biometrics Bulletin* 1:80–83. Pratt JW (1959) *J Am Stat Assoc* 54:655–667. Hollander M, Wolfe DA, *Nonparametric Statistical Methods*.

**Learn more:** [Wilcoxon signed-rank test (Wikipedia)](https://en.wikipedia.org/wiki/Wilcoxon_signed-rank_test), [Wilcoxon signed-rank test (Handbook of Biological Statistics)](https://www.biostathandbook.com/wilcoxonsignedrank.html).

**Related:** [[Ranks and ties]], [[Paired data]], [[Hodges-Lehmann estimate]], [[Paired t test]], [[Exact and approximate P values]], [[Mann-Whitney test]]

### Comparing three or more columns

#### Ordinary one-way ANOVA

<!-- opens: anova:ordinary, anova -->
<!-- equations: Ordinary one-way ANOVA -->

**In short:** Asks whether the means of three or more independent groups are all equal, assuming every group is Gaussian with the same SD. It splits the total variation into variation between the group means and variation within the groups, and compares the two with an F ratio.

**When to use it:** Three or more groups of different subjects (for example four treatments, each applied to separate biological replicates), with roughly Gaussian values and similar SDs. If the SDs may differ, use [[Welch's and Brown-Forsythe ANOVA]]. If the same subjects are measured in every group, use [[Repeated-measures one-way ANOVA]]. If the values are far from Gaussian and you cannot transform them, use the [[Kruskal-Wallis test]]. For two groups use the [[Unpaired t test]]. A small P tells you that the means are not all equal, not which ones differ: see [[Multiple comparisons after ANOVA]].

**What is done to your data:** Blank cells are left out, column by column. Every remaining value counts as one independent observation, so each value should be one biological replicate (see [[N and n]] and [[Pseudoreplication]]). Groups may have different numbers of values, but the table needs more values than groups (N > k). No outliers are removed and nothing is transformed. A warning appears if a group has fewer than 3 values or more than 30 (many values per group often means technical replicates).

**How it's calculated:** With k groups, group i having n_i values, mean ȳ_i and SD s_i, N values in all and ȳ the mean of all N values:

- Between-group [[Sum of squares]]: SS_between = Σ n_i (ȳ_i − ȳ)², with k − 1 [[Degrees of freedom|df]].
- Within-group (residual) sum of squares: SS_within = Σ_i Σ_j (y_ij − ȳ_i)², with N − k df. The total is SS_total = SS_between + SS_within, with N − 1 df.
- [[Mean square]]: MS = SS / df, for the between and the within rows.
- [[F ratio]]: F = MS_between / MS_within.
- [[P value]]: the chance that an F distribution with (k − 1, N − k) df exceeds the observed F, P = P(F(k − 1, N − k) > F). This is exact for Gaussian data and always one-tailed (large F only).
- [[R squared]] = SS_between / SS_total, the fraction of all the variation explained by the grouping.
- Two checks of the equal-SD assumption:
  - Brown-Forsythe test: the same ANOVA F, run on z = |y − median of its group|, with (k − 1, N − k) df. It is robust to non-Gaussian data.
  - Bartlett's test: K² = [(N − k) ln s_p² − Σ(n_i − 1) ln s_i²] / [1 + (Σ 1/(n_i − 1) − 1/(N − k)) / (3(k − 1))], where s_p² = SS_within / (N − k), compared with a chi-square distribution with k − 1 df. It is sensitive to non-Gaussian data. Both tests need at least 2 values in every group.

**Worked example:** Three biological replicates in each of three groups.

```example
Control: 4, 5, 6
Low: 6, 8, 10
High: 9, 11, 13
>>> one_way(table, "ordinary")
ss_between = 54
ss_within = 18
ss_total = 72
dfn = 2
dfd = 6
ms_between = 27
ms_within = 3
f = 9
p = 0.01563
r_squared = 0.75
bf_f = 0.4444
bf_p = 0.6607
bartlett = 0.8562
bartlett_p = 0.6517
```

By hand:

- Means: Control 5, Low 8, High 11. The mean of all 9 values is 8.
- SS_between = 3(5 − 8)² + 3(8 − 8)² + 3(11 − 8)² = 27 + 0 + 27 = 54, with k − 1 = 2 df.
- SS_within: Control (−1, 0, 1) gives 2; Low (−2, 0, 2) gives 8; High (−2, 0, 2) gives 8; total 18, with N − k = 9 − 3 = 6 df.
- SS_total = 54 + 18 = 72, with 8 df. Check: the squared distances of the 9 values from 8 add to 72.
- MS_between = 54 / 2 = 27 and MS_within = 18 / 6 = 3, so F = 27 / 3 = 9.
- P = P(F(2, 6) > 9) = 0.01563 (exactly 1/64 here). In Python, `scipy.stats.f.sf(9, 2, 6)`; in R, `pf(9, 2, 6, lower.tail = FALSE)`.
- R² = 54 / 72 = 0.75.
- Brown-Forsythe test: the values |y − median| are (1, 0, 1), (2, 0, 2) and (2, 0, 2). Their group means are 2/3, 4/3 and 4/3 and the overall mean is 10/9, so SS_between = 0.8889 and SS_within = 6. F = (0.8889 / 2) / (6 / 6) = 0.4444, P = 0.6607.

**Interpreting the results:** In the **ANOVA summary**, the **P value** is the chance of an F at least this large if all the group means were truly equal. Below alpha (usually 0.05) the sheet answers "Significant diff. among means" with Yes: at least one mean differs, but not which one. A large P means the data do not show a difference, not that there is none. **R squared** says how much of the variation the groups explain. The **ANOVA table** lists SS, DF, MS, F and P for "Treatment (between columns)", "Residual (within columns)" and "Total". In the **Brown-Forsythe test** and **Bartlett's test** sections, a small P means the SDs differ; the sheet then warns and suggests [[Welch's and Brown-Forsythe ANOVA]]. Both tests have little power with 3 values per group, so a large P there is weak reassurance. The **Group summary** gives n, mean, SD and SEM for each group.

**Options:** The design (unpaired) and Gaussian choice that select this test; alpha; the confidence level and the P value style; and the multiple comparisons or test for linear trend to run after the F test (see [[Multiple comparisons after ANOVA]] and [[Test for linear trend]]). In Advanced, you can set the residual df by hand (replaces N − k in the P value and the pooled comparisons), the SD that the multiple comparisons use (the F test is unchanged), and the number of comparisons that a Bonferroni-type correction counts. Each change is listed under "Changed from the default".

**Checked against:** R `aov` on `PlantGrowth` and `InsectSprays` (SS, MS, F, P), `bartlett.test`, and `car::leveneTest` centred on the median (Brown-Forsythe); `scipy.stats.levene` and `bartlett`.

**References:** Zar JH, *Biostatistical Analysis*, chapter 10. Brown MB, Forsythe AB (1974) *J Am Stat Assoc* 69:364–367.

**Learn more:** [One-way anova (Handbook of Biological Statistics)](https://www.biostathandbook.com/onewayanova.html), [One-factor ANOVA (NIST e-Handbook)](https://www.itl.nist.gov/div898/handbook/prc/section4/prc43.htm), [Analysis of variance (Wikipedia)](https://en.wikipedia.org/wiki/Analysis_of_variance).

**Related:** [[F ratio]], [[Sum of squares]], [[Mean square]], [[Degrees of freedom]], [[P value]], [[Multiple comparisons after ANOVA]], [[Welch's and Brown-Forsythe ANOVA]], [[Kruskal-Wallis test]]

#### Welch's and Brown-Forsythe ANOVA

<!-- opens: anova:welch, anova:brown_forsythe -->
<!-- equations: Welch's and Brown-Forsythe ANOVA -->

**In short:** Compares the means of three or more independent groups without assuming that the groups have the same SD. The sheet reports two tests, Welch's ANOVA (W) and Brown-Forsythe ANOVA (F\*), each with its own P value.

**When to use it:** Independent groups, roughly Gaussian, where the SDs may differ (for example a control with almost no spread next to treated groups that vary a lot). It is a safe default when you are unsure about equal SDs, and costs little power when the SDs are in fact equal. If the SDs are similar, [[Ordinary one-way ANOVA]] is the standard choice. For matched data use [[Repeated-measures one-way ANOVA]]; for non-Gaussian data use the [[Kruskal-Wallis test]].

**What is done to your data:** Blank cells are left out, column by column. Each group needs at least 2 values and some spread (SD > 0); otherwise the analysis stops with a message. Every value counts as one independent observation (see [[N and n]]). Nothing is removed or transformed. Group sizes may differ.

**How it's calculated:** With k groups, group i having n_i values, mean ȳ_i and variance s_i², N values in all:

- Welch's W:
  - weights w_i = n_i / s_i², and the weighted mean ȳ_w = Σ w_i ȳ_i / Σ w_i;
  - Λ = Σ (1 − w_i / Σw)² / (n_i − 1);
  - W = [Σ w_i (ȳ_i − ȳ_w)² / (k − 1)] / [1 + 2(k − 2)Λ / (k² − 1)];
  - the df are k − 1 and (k² − 1) / (3Λ), which is usually not a whole number.
- Brown-Forsythe F\*:
  - F\* = Σ n_i (ȳ_i − ȳ)² / Σ (1 − n_i / N) s_i², where ȳ is the mean of all values;
  - with c_i = (1 − n_i / N) s_i² / Σ (1 − n_j / N) s_j², the df are k − 1 and 1 / Σ (c_i² / (n_i − 1)).
  - With equal group sizes F\* equals the ordinary F, but with fewer denominator df.
- Each [[P value]] is the upper tail of an F distribution with the df above: P = P(F(df1, df2) > statistic). It is one-tailed and approximate (the F distribution is an approximation when SDs differ), though good for small groups.
- The result keeps Welch's P as the main P value.

**Worked example:** Three biological replicates in each group, with SDs 1, 2 and 3.

```example
A: 4, 5, 6
B: 6, 8, 10
C: 8, 11, 14
>>> one_way(table, "welch")
welch_f = 5.982
welch_dfn = 2
welch_dfd = 3.375
welch_p = 0.0777
bf_anova_f = 5.786
bf_anova_dfn = 2
bf_anova_dfd = 4
bf_anova_p = 0.06599
```

By hand:

- Means 5, 8, 11 (overall mean 8). Variances s² = 1, 4, 9. All n_i = 3.
- Welch weights: w = 3/1, 3/4, 3/9 = 3, 0.75, 0.3333, with sum 4.0833. ȳ_w = (3·5 + 0.75·8 + 0.3333·11) / 4.0833 = 24.667 / 4.0833 = 6.0408.
- Σ w_i (ȳ_i − ȳ_w)² = 3(1.0408)² + 0.75(1.9592)² + 0.3333(4.9592)² = 3.250 + 2.879 + 8.198 = 14.327. Divided by k − 1 = 2 this is 7.163.
- 1 − w_i/Σw = 0.2653, 0.8163, 0.9184. Λ = (0.2653² + 0.8163² + 0.9184²) / 2 = 0.7901.
- Denominator = 1 + 2(1)(0.7901) / 8 = 1.1975. W = 7.163 / 1.1975 = 5.982. Denominator df = 8 / (3 · 0.7901) = 3.375.
- P = P(F(2, 3.375) > 5.982) = 0.0777. In Python, `scipy.stats.f.sf(5.982, 2, 3.375)`; in R, `pf(5.982, 2, 3.375, lower.tail = FALSE)`.
- Brown-Forsythe: the numerator is Σ n_i (ȳ_i − ȳ)² = 54. The denominator is Σ (1 − 3/9) s_i² = (2/3)(1 + 4 + 9) = 9.333. F\* = 54 / 9.333 = 5.786.
- c_i = (1, 4, 9) / 14 = 0.0714, 0.2857, 0.6429. Σ c_i² / 2 = (0.0051 + 0.0816 + 0.4133) / 2 = 0.25, so the denominator df = 4. P = P(F(2, 4) > 5.786) = 0.06599.

**Interpreting the results:** The sheet has one section for each test. Each shows the statistic with its df, a P value and "Significant diff. among means". A small P (below alpha) says the means are not all equal; a large P says the data do not show a difference. The two tests usually agree. When they do not, say in your methods which one you chose before looking. In this example the groups with the bigger means also have the bigger SDs, so the noisy groups count for less and P is larger than the ordinary ANOVA's would be. To find which groups differ, use [[Games-Howell and Dunnett's T3]] (the default for this test); see [[Multiple comparisons after ANOVA]].

**Options:** Alpha, confidence level and P value style, and the multiple comparisons to follow. The test for linear trend is not available here because it assumes equal SDs. In Advanced, you can set the denominator df of Welch's W by hand (Brown-Forsythe F\* is unchanged) and the number of comparisons a Bonferroni-type correction counts.

**Checked against:** R `oneway.test` (Welch) on `PlantGrowth`; Welch's ANOVA size checked by simulation in the tests.

**References:** Welch BL (1951) *Biometrika* 38:330–336. Brown MB, Forsythe AB (1974) *J Am Stat Assoc* 69:364–367.

**Learn more:** [One-way analysis of variance (Wikipedia)](https://en.wikipedia.org/wiki/One-way_analysis_of_variance), [One-way anova (Handbook of Biological Statistics)](https://www.biostathandbook.com/onewayanova.html).

**Related:** [[Ordinary one-way ANOVA]], [[Games-Howell and Dunnett's T3]], [[Standard deviation]], [[Degrees of freedom]], [[F ratio]], [[Multiple comparisons after ANOVA]]

#### Repeated-measures one-way ANOVA

<!-- opens: anova:repeated_measures -->
<!-- equations: Repeated-measures ANOVA -->

**In short:** Asks whether the means of three or more conditions differ when the same subjects are measured in every condition. Differences between subjects are removed from the error, which usually makes the test more sensitive than [[Ordinary one-way ANOVA]]. By default the degrees of freedom are shrunk by the Geisser-Greenhouse epsilon, so P stays valid when sphericity does not hold.

**When to use it:** Each row is one subject (one biological replicate) measured under every condition, for example one culture imaged before, 1 h after and 2 h after a drug. Values should be roughly Gaussian. For two conditions use the [[Paired t test]]. For non-Gaussian matched data use the [[Friedman test]]. If some rows have a blank, the same menu item fits a [[Mixed-effects model]] instead.

**What is done to your data:** Row i of every column must be the same subject, so mark the table as Paired. The test needs complete rows. If every row is complete, all values are used. If any row has a blank, the sheet is produced by the [[Mixed-effects model]] (its title says so, and a warning says how many rows had a blank). (In code, `missing="exclude"` drops those rows instead and the warning gives the count; the dialog does not offer it.) At least 2 complete rows are needed. Nothing is transformed.

**How it's calculated:** With n subjects (rows), k conditions (columns), ȳ the mean of all values, ȳ_.j the mean of column j and ȳ_i. the mean of row i:

- SS_treatment = n Σ_j (ȳ_.j − ȳ)², with k − 1 [[Degrees of freedom|df]].
- SS_subjects = k Σ_i (ȳ_i. − ȳ)², with n − 1 df.
- SS_total = Σ (y − ȳ)², with nk − 1 df, and SS_residual = SS_total − SS_treatment − SS_subjects, with (n − 1)(k − 1) df.
- [[Mean square]]s are SS / df. F = MS_treatment / MS_residual.
- [[Sphericity]] means that the differences between every pair of columns have the same variance. The [[Geisser-Greenhouse epsilon]] measures how far the data depart from it: ε = (tr S\*)² / ((k − 1) tr(S\*²)), where S is the k × k covariance matrix of the columns and S\* = CSC is its double-centred form (C = I − J/k, so S\*_ij = S_ij − row mean_i − row mean_j + grand mean of S). 1/(k − 1) ≤ ε ≤ 1, and 1 means no departure. With two columns ε = 1. It is always reported.
- With the correction on (the default), both df are multiplied by ε, so the P value is P(F(ε(k − 1), ε(n − 1)(k − 1)) > F). The uncorrected P (ε = 1) and the corrected P are both kept in the stored values. The P is exact for Gaussian data, one-tailed (large F only).
- Was the matching effective? F = MS_subjects / MS_residual with (n − 1, (n − 1)(k − 1)) df. A large F means the subjects differ a lot from one another, so matching helped.
- Two [[R squared]] values. Treatment R² = SS_treatment / (SS_treatment + SS_residual), which leaves the subjects out (with two columns it equals the paired t test's R², and F = t²). Matching R² = SS_subjects / SS_total.

**Worked example:** Four subjects measured at three times.

```example
Before: 5, 6, 8, 9
1 h: 7, 9, 9, 11
2 h: 9, 9, 13, 13
>>> table = ColumnTable.from_columns(data, paired=True)
>>> one_way(table, "repeated_measures")
ss_treatment = 32
ss_subjects = 30
ss_residual = 4
ss_total = 66
ms_treatment = 16
ms_subjects = 10
ms_residual = 0.6667
f = 24
epsilon = 0.5
dfn_uncorrected = 2
dfd_uncorrected = 6
p_uncorrected = 0.001372
dfn = 1
dfd = 3
p = 0.01628
r_squared = 0.8889
r_squared_matching = 0.4545
f_subjects = 15
p_subjects = 0.003401
```

By hand:

- Column means 7, 9, 11, grand mean 9. Row means (subjects 1 to 4): 7, 8, 10, 11.
- SS_treatment = 4[(7 − 9)² + 0 + (11 − 9)²] = 32, with 2 df. SS_subjects = 3[(7 − 9)² + (8 − 9)² + (10 − 9)² + (11 − 9)²] = 3 · 10 = 30, with 3 df.
- SS_total = 66 (add up the 12 squared distances from 9), so SS_residual = 66 − 32 − 30 = 4, with 3 · 2 = 6 df.
- MS_treatment = 32/2 = 16, MS_subjects = 30/3 = 10, MS_residual = 4/6 = 0.6667. F = 16 / 0.6667 = 24. Matching F = 10 / 0.6667 = 15.
- Uncorrected P = P(F(2, 6) > 24) = 0.001372. In R, `pf(24, 2, 6, lower.tail = FALSE)`.
- The covariance matrix of the three columns has rows (3.333, 2.667, 4), (2.667, 2.667, 2.667) and (4, 2.667, 5.333). Its row means are 3.333, 2.667 and 4, and its grand mean is 3.333. Double-centring gives S\* = (2/3) times the matrix with rows (0, 0, 0), (0, 1, −1) and (0, −1, 1). Then tr S\* = 4/3 and tr(S\*²) = 16/9, so ε = (16/9) / (2 · 16/9) = 0.5.
- Corrected df: 2 · 0.5 = 1 and 6 · 0.5 = 3. P = P(F(1, 3) > 24) = 0.01628. With 1 numerator df, F = t², so this is also `2 * scipy.stats.t.sf(math.sqrt(24), 3)`.
- R² (treatment) = 32 / (32 + 4) = 0.8889. R² (matching) = 30 / 66 = 0.4545. Matching P = P(F(3, 6) > 15) = 0.003401.

**Interpreting the results:** In the **summary**, "Assume sphericity" shows No when the Geisser-Greenhouse correction is on. The **P value** is the chance of an F this large if the conditions had equal means. Here the correction raised P from 0.0014 to 0.016, because ε is at its lowest possible value (0.5) with 3 columns, so sphericity is badly broken in this tiny sample; with 4 subjects ε is a very rough estimate. **Geisser-Greenhouse's epsilon** below about 0.75 is a sign of a clear departure. Under **Was the matching effective?**, a small P means that the subjects really differ from one another and pairing them was worthwhile. The **ANOVA table** shows the treatment df and the residual DF after multiplying by ε, which is why they are not whole numbers in general. If the sheet is titled "Mixed-effects model (REML)", see [[Mixed-effects model]].

**Options:** The paired design and Gaussian choices; the Geisser-Greenhouse checkbox ("Don't assume sphericity", ticked by default); alpha, confidence level, P value style; the multiple comparisons or test for linear trend to follow. Multiple comparisons use the error term of the whole ANOVA when sphericity is assumed, but only the two columns being compared when the correction is on (see [[Multiple comparisons after ANOVA]]). In Advanced, you can set the residual df (applied before ε), the sphericity ε itself (between 1/(k − 1) and 1, instead of the estimated one), the SD for the multiple comparisons, and the number of comparisons a Bonferroni-type correction counts. These are not available when rows have blanks. Changes are listed under "Changed from the default".

**Checked against:** statsmodels `AnovaRM`; the Geisser-Greenhouse epsilon was checked in the tests against an independent calculation by orthonormal contrasts.

**References:** Geisser S, Greenhouse SW (1958) *Ann Math Stat* 29:885–891. Keppel G, *Design and Analysis*, for the treatment R². Maxwell SE, Delaney HD, *Designing Experiments and Analyzing Data*, pp. 552–555.

**Learn more:** [Repeated measures ANOVA (Wikipedia)](https://en.wikipedia.org/wiki/Repeated_measures_design), [Mauchly's test of sphericity (Wikipedia)](https://en.wikipedia.org/wiki/Mauchly%27s_sphericity_test).

**Related:** [[Sphericity]], [[Geisser-Greenhouse epsilon]], [[Paired data]], [[Mixed-effects model]], [[Friedman test]], [[Ordinary one-way ANOVA]], [[Multiple comparisons after ANOVA]]

#### Mixed-effects model

<!-- opens: anova:mixed -->
<!-- equations: Mixed-effects model -->

**In short:** The repeated-measures analysis for matched data in which some values are missing. Each subject gets its own baseline level (a random effect), and every value that was measured is used, so a subject with one blank is not thrown away. With no blanks it gives the same F and P as [[Repeated-measures one-way ANOVA]] assuming sphericity.

**When to use it:** The same subjects measured in three or more conditions, with a blank cell in some rows. You do not choose it from the menu. Choose "Repeated-measures ANOVA" under "Compare three or more columns"; when a row has a blank, the engine fits this model instead and the sheet is titled "Mixed-effects model (REML)". If the blanks are many or tied to the condition itself (a value is missing because it was too low to measure), the model may be misleading.

**What is done to your data:** Every value that is present is used; blanks are simply absent. Row i is one subject, so mark the table as Paired. A row that is entirely blank is ignored. Every column needs at least one value, at least 2 subjects are needed, and there must be more values than subjects plus groups − 1 (so the denominator df is at least 1). Nothing is transformed.

**How it's calculated:**

- The model is value = group mean + subject effect + error. The subject effect and the error are Gaussian with variances σ²_subject and σ²_error. It is fitted by restricted maximum likelihood (REML) with statsmodels' `MixedLM`, with one random intercept per row.
- Fixed effect of treatment: a Wald F test that all k group means are equal. With β the fitted group means and V = (X′V̂⁻¹X)⁻¹ their covariance at the REML variance estimates, F = (Lβ)′ (L V L′)⁻¹ (Lβ) / (k − 1), where L compares the first mean with each other mean.
- The df are DFn = k − 1 and DFd = (number of values) − (number of subjects) − (k − 1), the "between-within" df. This is a choice of this engine; other programs use other denominator df, so the P value may differ slightly from theirs. A warning on every sheet says so.
- Geisser-Greenhouse (ticked by default): ε is computed as for repeated measures (see [[Geisser-Greenhouse epsilon]]) but from the covariance matrix with each entry taken over the rows where both columns have values. Both df are multiplied by ε. If a pair of columns has fewer than 2 rows in common, ε cannot be estimated and P is left uncorrected, with a warning. There is no standard way to estimate ε with missing values, so this is this engine's choice.
- Random effects: the SD and variance of the subject effect and of the residual.
- Was the matching effective? A likelihood-ratio test of σ²_subject = 0: twice the difference in REML log likelihood, compared with a chi-square distribution with 1 df. It is conservative because the variance cannot be below 0.
- The sheet also lists the fitted group means with their standard errors. When a subject is missing a value, a fitted mean can differ from the plain mean of that column, because the model adjusts for how high or low the subjects with that value are.

**Worked example:** The same data as in [[Repeated-measures one-way ANOVA]]. First with no blanks, where every number can be checked by hand, with sphericity assumed:

```example
Before: 5, 6, 8, 9
1 h: 7, 9, 9, 11
2 h: 9, 9, 13, 13
>>> table = ColumnTable.from_columns(data, paired=True)
>>> one_way(table, "mixed", geisser_greenhouse=False)
f = 24.00
dfn = 2
dfd = 6
p = 0.001372
var_subject = 3.111
var_residual = 0.6667
```

By hand:

- This is the repeated-measures ANOVA with sphericity assumed: F = MS_treatment / MS_residual = 16 / 0.6667 = 24 with (2, 6) df, so P = 0.001372. DFd = 12 values − 4 subjects − 2 = 6.
- With no blanks the REML variance estimates equal the ANOVA ones: σ²_error = MS_residual = 0.6667, and σ²_subject = (MS_subjects − MS_residual) / k = (10 − 0.6667) / 3 = 3.111.

Now the last subject's 2 h value is missing:

```example
Before: 5, 6, 8, 9
1 h: 7, 9, 9, 11
2 h: 9, 9, 13,
>>> table = ColumnTable.from_columns(data, paired=True)
>>> one_way(table, "mixed", geisser_greenhouse=False)
f = 15.70
dfn = 2
dfd = 5
p = 0.006997
var_subject = 2.970
var_residual = 0.8037
lr_matching = 4.847
p_matching = 0.02769
```

- DFd = 11 values − 4 subjects − 2 = 5. The F, the variance components and the likelihood ratio come from the fitted model and cannot be done on paper. Check them with an independent fit: in Python, `statsmodels.formula.api.mixedlm("value ~ 0 + C(group)", long, groups="subject").fit(reml=True)` on the 11 values in long form; in R, `lme4::lmer(value ~ 0 + group + (1 | subject), REML = TRUE)`. The P value is `scipy.stats.f.sf(F, 2, 5)`.
- With the Geisser-Greenhouse correction left on (the default), the engine estimates ε = 0.5 from the pairwise-complete covariances and reports P with (1, 2.5) df, 0.03986.

**Interpreting the results:** **Fixed effects**: "Treatment: F (DFn, DFd)" with its P value says whether the group means differ. Read it as for repeated measures. "Geisser-Greenhouse's epsilon" shows the estimated ε. **Random effects** list the SD and variance between subjects and the residual SD and variance; the subject SD is how much the subjects' baselines differ. **Was the matching effective?** gives chi-square and P: a small P means the subjects differ and the matching helped. **Fitted means** list each group's mean ± its SE from the model. A warning appears when the subject variance is estimated as zero (then the model is just an ordinary ANOVA) or when the fit reports convergence problems.

**Options:** The same dialog as repeated-measures ANOVA: the Geisser-Greenhouse checkbox, alpha, confidence level, P value style and the multiple comparisons. The Advanced residual df, ε and SD are not available with blanks; the comparisons use the model's fitted means, their covariance and the same between-within df, even with the correction on. You cannot choose another covariance structure: subjects are random and columns are fixed.

**Checked against:** A direct REML fit of the model written out independently in the tests; with no blanks it equals the repeated-measures ANOVA. The denominator-df rule and the epsilon with missing values are choices that have not been compared with other programs.

**References:** Laird NM, Ware JH (1982) *Biometrics* 38:963–974. Patterson HD, Thompson R (1971) *Biometrika* 58:545–554 (REML).

**Learn more:** [Mixed model (Wikipedia)](https://en.wikipedia.org/wiki/Mixed_model), [Restricted maximum likelihood (Wikipedia)](https://en.wikipedia.org/wiki/Restricted_maximum_likelihood).

**Related:** [[Fixed and random effects]], [[Variance components]], [[Intraclass correlation]], [[Repeated-measures one-way ANOVA]], [[Geisser-Greenhouse epsilon]], [[Paired data]]

#### Kruskal-Wallis test

<!-- opens: anova:kruskal_wallis -->
<!-- equations: Kruskal-Wallis test -->

**In short:** The rank-based counterpart of [[Ordinary one-way ANOVA]]. It ranks all the values together and asks whether the groups tend to have different ranks. It does not assume a Gaussian distribution.

**When to use it:** Three or more independent groups whose values are far from Gaussian and cannot be transformed (scores, counts, percentages piled near 0 or 100). With very small groups it has little power: see below. For two groups use the [[Mann-Whitney test]]; for matched data use the [[Friedman test]]. Follow a small P with [[Dunn's test]].

**What is done to your data:** Blank cells are left out, column by column. The remaining values are replaced by their ranks across all groups, so only the order of the values matters, not their size (see [[Ranks and ties]]). Each group needs at least 1 value. If every value is identical the test is undefined and stops. A warning appears for a group of more than 30 values (likely technical replicates).

**How it's calculated:** With N values in all, k groups, and R_i the sum of the ranks of group i (n_i values):

- Rank all N values together from 1 (smallest); tied values get the average of the ranks they span.
- H = [12 / (N(N + 1)) · Σ R_i² / n_i − 3(N + 1)] / [1 − Σ(t³ − t) / (N³ − N)], where t is the size of each set of tied values (the denominator is 1 when there are no ties).
- [[P value]], two ways (the sheet says which):
  - Exact: P is the share of all ways to split the observed ranks into groups of the observed sizes that give an H at least as large as the observed one. Ties are included in this count. It is used when the number of distinct splits, N! / (Π n_i! · Π m_s!) (m_s = number of groups of size s), is at most 2 000 000. The cutoff is this engine's choice. For example 5 groups of 3 (1,401,400 splits) are exact; 4 groups of 4 (2,627,625 splits) and 6 groups of 3 are not.
  - Approximate otherwise: P = P(χ²(k − 1) > H). With a group of fewer than 5 values the approximation can be poor, and a warning appears.
- The sheet also lists each group's median, sum of ranks and mean rank.
- Smallest possible P: with few values even complete separation of the groups cannot give a small exact P. For example three groups of 2 cannot go below 6/90 = 0.067. When that smallest P is not below alpha, a warning says no data could be significant (see [[Exact and approximate P values]]).

**Worked example:** Three groups of three.

```example
A: 12, 15, 18
B: 14, 20, 22
C: 25, 28, 30
>>> one_way(table, "kruskal_wallis")
h = 5.956
df = 2
p = 0.025
p_chi2 = 0.05091
exact = True
rank_sums[0] = 8
rank_sums[1] = 13
rank_sums[2] = 24
mean_ranks[0] = 2.667
mean_ranks[1] = 4.333
mean_ranks[2] = 8
medians[1] = 20
```

By hand:

- Ordered values: 12 (A) 1, 14 (B) 2, 15 (A) 3, 18 (A) 4, 20 (B) 5, 22 (B) 6, 25 (C) 7, 28 (C) 8, 30 (C) 9. Rank sums: A = 1 + 3 + 4 = 8, B = 2 + 5 + 6 = 13, C = 7 + 8 + 9 = 24. They add to 45 = 9 · 10 / 2.
- No ties, so the correction is 1. H = 12 / (9 · 10) · (8² + 13² + 24²) / 3 − 3 · 10 = 0.13333 · 269.67 − 30 = 5.956.
- Exact P: there are 9! / (3! 3! 3!) = 1680 ways to give the 9 ranks to the labelled groups A, B and C. Count those with Σ R_i²/n_i ≥ (64 + 169 + 576) / 3 = 269.67. The count is 42, so P = 42 / 1680 = 0.025. In Python, loop over `itertools.combinations(range(1, 10), 3)` for A, then for B from the remaining ranks, and compare the sums.
- The chi-square approximation gives P(χ²(2) > 5.956) = exp(−5.956 / 2) = 0.0509, which is noticeably larger than the exact P. This is why the exact test is used for small groups. In R, `kruskal.test` uses the chi-square approximation, so `pchisq(5.956, 2, lower.tail = FALSE)` matches it.

**Interpreting the results:** **P value** is the chance of an H this large if all the groups came from the same distribution. "Exact or approximate P value?" tells you which of the two calculations was used. **Kruskal-Wallis statistic** is H. "Do the medians vary signif." answers Yes when P is below alpha. Strictly the test compares ranks; it is a comparison of medians only if the groups have similar shapes and spreads. The **Group summary** gives each group's n, median, sum of ranks and mean rank: the group with the largest mean rank tends to have the largest values. The test does not say which groups differ; see [[Dunn's test]] and [[Multiple comparisons after ANOVA]].

**Options:** Alpha, P value style, and the multiple comparisons to follow (Dunn's test and the false discovery rate methods; the confidence level and the test for linear trend do not apply). Whether P is exact or approximate is decided by the group sizes, and is not set in the dialog.

**Checked against:** R `kruskal.test` on `PlantGrowth`, `InsectSprays` (many ties) and the mucociliary efficiency example in `?kruskal.test`; `scipy.stats.kruskal`; the exact P against full enumeration in the tests.

**References:** Kruskal WH, Wallis WA (1952) *J Am Stat Assoc* 47:583–621. Hollander M, Wolfe DA, *Nonparametric Statistical Methods*.

**Learn more:** [Kruskal-Wallis test (Handbook of Biological Statistics)](https://www.biostathandbook.com/kruskalwallis.html), [Kruskal-Wallis test (Wikipedia)](https://en.wikipedia.org/wiki/Kruskal%E2%80%93Wallis_test).

**Related:** [[Ranks and ties]], [[Exact and approximate P values]], [[Mann-Whitney test]], [[Dunn's test]], [[Ordinary one-way ANOVA]], [[Friedman test]]

#### Friedman test

<!-- opens: anova:friedman -->
<!-- equations: Friedman test -->

**In short:** The rank-based counterpart of [[Repeated-measures one-way ANOVA]]. Within each subject the values are ranked across the conditions, and the test asks whether some conditions tend to get higher ranks than others. It does not assume a Gaussian distribution.

**When to use it:** The same subjects measured in three or more conditions, with values that are far from Gaussian and cannot be transformed. For two conditions use the [[Wilcoxon matched-pairs test]]; for independent groups use the [[Kruskal-Wallis test]]. Follow a small P with [[Dunn's test]].

**What is done to your data:** Row i must be one subject, so mark the table as Paired. Rows with a blank are left out entirely (the warning says how many), because the ranks within a row need every condition. At least 2 complete rows are needed. Within each row the values are replaced by ranks 1 to k (ties get the average rank; see [[Ranks and ties]]). Only the order inside a row matters, so the test ignores differences in level between subjects.

**How it's calculated:** With n complete rows, k conditions and R_j the sum of the ranks of column j:

- Rank each row from 1 to k.
- Q = 12 Σ (R_j − n(k + 1)/2)² / [n k (k + 1) − Σ(t³ − t) / (k − 1)], where t is the size of each set of ties inside a row (the correction is 0 without ties). This is the tie-corrected form used by R's `friedman.test`.
- [[P value]], two ways (the sheet says which):
  - Exact unless (k!)^n > 10⁹, which allows 3 conditions up to 11 subjects, 4 up to 6, and 5 up to 4. The exact P assumes the ranks inside each row are in a random order, all k! orders equally likely, independently across rows. P is the share of those equally likely outcomes with Σ R_j² at least the observed value.
  - Otherwise P = P(χ²(k − 1) > Q). With fewer than 10 subjects the approximation can be inaccurate, and a warning appears.
- The smallest possible exact P is (k!)^(1−n), reached when every row ranks the conditions in the same order. For example 2 subjects and 3 conditions cannot go below 1/6. If that floor is not below alpha, a warning says so.

**Worked example:** Four subjects measured in three conditions.

```example
A: 5, 6, 8, 10
B: 7, 9, 9.5, 9
C: 9, 8, 13, 12
>>> table = ColumnTable.from_columns(data, paired=True)
>>> one_way(table, "friedman")
statistic = 4.5
df = 2
p = 0.125
p_chi2 = 0.1054
exact = True
rank_sums[0] = 5
rank_sums[1] = 8
rank_sums[2] = 11
mean_ranks[0] = 1.25
mean_ranks[2] = 2.75
```

By hand:

- Ranks within each row: subject 1 (5, 7, 9) gives 1, 2, 3; subject 2 (6, 9, 8) gives 1, 3, 2; subject 3 (8, 9.5, 13) gives 1, 2, 3; subject 4 (10, 9, 12) gives 2, 1, 3. Rank sums: A = 5, B = 8, C = 11. They add to 24 = 4 · 3 · 4 / 2.
- No ties, so Q = 12 [(5 − 8)² + (8 − 8)² + (11 − 8)²] / (4 · 3 · 4) = 12 · 18 / 48 = 4.5. (The expected rank sum under no effect is n(k + 1)/2 = 8.)
- Exact P: each row can be ranked in 3! = 6 ways, so there are 6⁴ = 1296 equally likely outcomes. Count the outcomes whose Σ R_j² is at least 5² + 8² + 11² = 210. The count is 162, so P = 162 / 1296 = 0.125. In Python, loop over `itertools.product(itertools.permutations([1, 2, 3]), repeat=4)`, add up the columns and count.
- The chi-square approximation gives P(χ²(2) > 4.5) = exp(−4.5 / 2) = 0.1054.
- The smallest possible exact P here is 6⁻³ = 0.0046, so the test could in principle reach significance with 4 subjects.

**Interpreting the results:** **P value** is the chance of rank sums this uneven if the conditions had no effect. "Exact or approximate P value?" says which calculation was used. **Friedman statistic** is Q. "Are the medians signif. different" answers Yes when P is below alpha; as for [[Kruskal-Wallis test]], this is strictly about ranks. The **Group summary** gives each condition's median and sum of ranks: a higher sum means the condition tends to rank higher within subjects. In the example, C ranks highest, but with 4 subjects the difference is not significant. The test does not say which conditions differ; see [[Dunn's test]] and [[Multiple comparisons after ANOVA]].

**Options:** Alpha, P value style, and the multiple comparisons to follow (Dunn's test and the false discovery rate methods). The confidence level does not apply. Exact or approximate P follows from the numbers of subjects and conditions and is not set in the dialog.

**Checked against:** R `friedman.test` on the RoundingTimes example in `?friedman.test`; `scipy.stats.friedmanchisquare`; the exact P against full enumeration in the tests. Whether other packages apply the tie correction is not yet compared.

**References:** Friedman M (1937) *J Am Stat Assoc* 32:675–701. Hollander M, Wolfe DA, *Nonparametric Statistical Methods*.

**Learn more:** [Friedman test (Wikipedia)](https://en.wikipedia.org/wiki/Friedman_test), [StatQuest](https://statquest.org/).

**Related:** [[Ranks and ties]], [[Exact and approximate P values]], [[Paired data]], [[Wilcoxon matched-pairs test]], [[Dunn's test]], [[Repeated-measures one-way ANOVA]], [[Kruskal-Wallis test]]

### Comparing groups after ANOVA

#### Multiple comparisons after ANOVA

<!-- equations: Multiple comparisons -->

**In short:** After an ANOVA says that some group means differ, multiple comparisons say which pairs differ, while keeping the chance of any false alarm in the whole set of comparisons at alpha.

**When to use it:** You have three or more groups and want to know which groups differ, not only whether they differ. Choose the comparisons before you look at the data:

- **Every pair of groups**: use Tukey (or Games-Howell after [[Welch's and Brown-Forsythe ANOVA]], or Dunn's test after [[Kruskal-Wallis test]]).
- **Each group against a control**: use Dunnett (see [[Dunnett's test]]).
- **A few pairs chosen in advance**: use Šidák, Holm-Šidák or Bonferroni on just those pairs.
- **Many comparisons where a few false discoveries are acceptable**: use a false discovery rate method (see [[False discovery rate (q values)]]).

**What is done to your data:** The comparisons start from the numbers the ANOVA already used: each group's mean (mean rank for Dunn's test), the number of values in each group, and the ANOVA's error term. Blank cells are left out as in the ANOVA. No values are removed or transformed. The comparisons are made whether or not the ANOVA's own P is below alpha, so you decide whether a non-significant ANOVA should stop you.

**How it's calculated:** The comparisons are grouped into **families**. All the comparisons in one family share one alpha: the chance of one or more false alarms in the family is at most alpha (see [[Family-wise error rate]]). For every pair "A vs. B":

- The difference is mean_A − mean_B. Against a reference group the row is "reference vs. group", so the difference is reference − group.
- The standard error comes from the error term of the analysis before it. After an ordinary ANOVA, SE = √(MS_residual · (1/n_A + 1/n_B)) with the residual df (N − k). After [[Repeated-measures one-way ANOVA]] with the Geisser-Greenhouse correction (the default), each pair uses only its own two columns, like a paired t test (SE = SD of the differences / √n, df = n − 1). After Welch's ANOVA, SE = √(s_A²/n_A + s_B²/n_B) with Welch's df for that pair.
- t = difference / SE, and the unadjusted two-tailed P comes from the t distribution (a normal distribution for Dunn's test).
- Then the method turns that into an adjusted P. m is the number of comparisons in the family:

      Tukey / Games-Howell:  q = √2 · |t|, P_adj = P(studentized range of k means ≥ q)
      Dunnett:               P_adj = P(largest |t| of the k − 1 comparisons ≥ |t|)
      Bonferroni:            P_adj = min(1, m · P)
      Šidák:                 P_adj = 1 − (1 − P)^m
      Holm-Šidák:            sort P ascending; P_adj(r) = largest, over s ≤ r, of 1 − (1 − P(s))^(m − s + 1)
      Fisher's LSD:          P_adj = P (no correction)

- A comparison is **significant** when its adjusted P is below alpha.
- The confidence interval is difference ± (critical value) · SE. The critical value is chosen so that **all** the intervals in the family are right together at the confidence level (95% by default). They are wider than the unadjusted intervals. Tukey uses the studentized range, Dunnett the multivariate t, Bonferroni and Šidák a t value at a tighter per-interval level (1 − α/m and (1 − α)^(1/m)).
- Holm-Šidák, Newman-Keuls, Dunn's test and the FDR methods give no confidence interval. Newman-Keuls also gives no adjusted P.

What an **adjusted P** means: it is the smallest family-wise alpha at which that comparison would still be called significant. If the adjusted P of "A vs. B" is 0.03, the whole family can be run at alpha = 0.03 and A vs. B is still significant. It is already corrected for the other comparisons, so compare it with alpha directly (do not multiply it again). See [[Adjusted P value]].

**Worked example:** Three groups of three biological replicates, every pair compared with Tukey's test.

```example
A: 3, 5, 7
B: 5, 7, 9
C: 9, 11, 13
>>> one_way(table, "ordinary", comparisons="all", method="tukey")
comparison_method = tukey
n_families = 1
comparisons_per_family[0] = 3
comparisons[0].difference = -2
comparisons[0].se = 1.633
comparisons[0].df = 6
comparisons[0].statistic = 1.732
comparisons[0].p = 0.2666
comparisons[0].p_adjusted = 0.4827
comparisons[0].ci_low = -7.010
comparisons[0].ci_high = 3.010
comparisons[1].p = 0.01040
comparisons[1].p_adjusted = 0.02423
comparisons[2].p = 0.04983
comparisons[2].p_adjusted = 0.1089
```

By hand:

- The means are 5, 7 and 11, and every SD is 2, so MS_residual = 4 with N − k = 9 − 3 = 6 df.
- For A vs. B: SE = √(4 · (1/3 + 1/3)) = 1.633. The difference is 5 − 7 = −2, so t = −2 / 1.633 = −1.225 and the unadjusted P = 2 · P(T₆ > 1.225) = 0.2666 (`2 * scipy.stats.t.sf(1.2247, 6)`).
- Tukey: q = √2 · 1.225 = 1.732. P_adj = P(range of 3 means ≥ 1.732 with 6 df) = 0.4827. In Python, `scipy.stats.studentized_range.sf(1.732, 3, 6)`; in R, `ptukey(1.732, 3, 6, lower.tail = FALSE)`.
- The 95% critical q for 3 means and 6 df is 4.3395, so the half-width is 4.3395 / √2 · 1.633 = 5.010 and the interval is −2 ± 5.010 = −7.010 to 3.010.
- The same table for the other methods, for the pair B vs. C (unadjusted P = 0.04983) and A vs. C (unadjusted P = 0.01040):

      method          A vs. C    B vs. C
      none (LSD)      0.0104     0.0498
      Tukey           0.0242     0.1089
      Holm-Šidák      0.0309     0.0972
      Šidák           0.0309     0.1422
      Bonferroni      0.0312     0.1495

  Bonferroni: 3 · 0.01040 = 0.0312. Šidák: 1 − (1 − 0.01040)³ = 0.0309. Holm-Šidák sorts the three P values (0.01040, 0.04983, 0.2666) and uses the factors 3, 2, 1: 1 − (1 − 0.01040)³ = 0.0309, then 1 − (1 − 0.04983)² = 0.0972, then 0.2666. Without any correction B vs. C looks significant; after correcting for three comparisons, none of the methods keeps it so.

**Interpreting the results:** The sheet lists, for each comparison, the mean difference, its confidence interval, the adjusted P (or q for FDR methods), and "Significant?" with the stars. At the top it gives the number of families, the number of comparisons per family, and alpha. Read a comparison as: with the whole family held to alpha, is this pair's difference distinguishable from 0? An interval that excludes 0 goes with an adjusted P below alpha. A non-significant comparison does not show that two groups are equal; with small groups the intervals are wide. Do not run extra comparisons and then correct only for some of them: the family is every comparison you would have been willing to report.

**Options:** The goal: none, every pair, each group against one or two reference groups, selected pairs, or a test for linear trend. The method list shows only what is valid for the design: Tukey, Newman-Keuls, Dunnett, Šidák, Bonferroni, Holm-Šidák, Fisher's LSD and the FDR methods after an ANOVA with equal SDs; Games-Howell, Dunnett's T3, Tamhane's T2, Fisher's LSD (Welch's t) and the FDR methods after Welch's ANOVA; Dunn's test and the FDR methods after a rank test. The recommended method is first in the list: Tukey for every pair, Dunnett against a control, Šidák for selected pairs. With two reference groups, **Each reference is its own family** (the default) corrects each reference's k − 1 comparisons separately; **Both references as one family** corrects them together, which is stricter. Fisher's LSD makes no correction at all; use it only for one or two comparisons planned in advance. Newman-Keuls is offered for old analyses; it does not control the family-wise error rate, so prefer Tukey. Holm's plain (Bonferroni) step-down is available only in [[Analyze a stack of P values]]. In Advanced: the SD used for the comparisons (ordinary and repeated-measures ANOVA only; the pooled SD by default) and, for Bonferroni, Šidák, Holm-Šidák and Dunn, the number of comparisons the correction counts (at least the number made). A changed setting is listed under "Changed from the default".

**Checked against:** R `TukeyHSD` (PlantGrowth, and the `warpbreaks` example in `?TukeyHSD`); `scipy.stats.tukey_hsd` for unequal n and `scipy.stats.dunnett`; statsmodels `multipletests` for Šidák, Bonferroni, Holm-Šidák and the FDR methods; Dunnett's published critical values; simulations of the Dunnett and maximum-modulus probabilities; Games-Howell and Tamhane's T2 written out independently in the tests.

**References:** Tukey JW (1949) *Biometrics* 5:99–114. Kramer CY (1956) *Biometrics* 12:307–310. Šidák Z (1967) *J Am Stat Assoc* 62:626–633. Holm S (1979) *Scand J Stat* 6:65–70. Maxwell SE, Delaney HD, *Designing Experiments and Analyzing Data*, pp. 552–555 (repeated measures).

**Learn more:** [Multiple comparisons (Handbook of Biological Statistics)](https://www.biostathandbook.com/multiplecomparisons.html), [Multiple comparisons problem (Wikipedia)](https://en.wikipedia.org/wiki/Multiple_comparisons_problem), [Tukey's range test (Wikipedia)](https://en.wikipedia.org/wiki/Tukey%27s_range_test).

**Related:** [[Multiple comparisons]], [[Family-wise error rate]], [[Adjusted P value]], [[Alpha]], [[Ordinary one-way ANOVA]], [[Dunnett's test]], [[Games-Howell and Dunnett's T3]], [[Dunn's test]], [[False discovery rate (q values)]], [[Compact letter display]]

#### Dunnett's test

<!-- equations: Multiple comparisons -->

**In short:** Compares every group with one control group and keeps the chance of any false alarm across those comparisons at alpha.

**When to use it:** One group is a control (untreated, vehicle, wild type) and you want to know which other groups differ from it. You do not care about the pairs among the treated groups. Use it after an ordinary or repeated-measures ANOVA with similar SDs. If you want every pair, use Tukey (see [[Multiple comparisons after ANOVA]]). If the SDs differ, use Dunnett's T3 (see [[Games-Howell and Dunnett's T3]]).

**What is done to your data:** The control is chosen in the dialog (the first column in the suggested setup). The group means, group sizes and the ANOVA's pooled error term (MS_residual with N − k df) are used; blanks are left out as in the ANOVA. Each of the other k − 1 groups is compared with the control. One or two controls can be chosen. Dunnett's test itself is defined for one control per family, so with two controls each control is its own family (the default); combining both controls into one family is available with any method except Dunnett's.

**How it's calculated:** For each group j and control 0:

- Difference = mean_0 − mean_j (reference minus group). SE = √(MS_residual · (1/n_0 + 1/n_j)), df = N − k.
- t = difference / SE.
- The k − 1 t statistics share the control's mean, so they are correlated: ρᵢⱼ = √(nᵢnⱼ / ((nᵢ + n₀)(nⱼ + n₀))), which is 0.5 when all groups have the same n.
- Adjusted P = P(max |T₁|, …, |T_{k−1}| ≥ |t|), the probability, under the null, that the largest of the k − 1 t statistics is at least as extreme as the observed one. This is a multivariate t probability. It is computed by Dunnett's (1955) one-factor integral (Gauss-Hermite over the shared normal part, Gauss-Legendre over the error SD). Designs without that structure (repeated measures without sphericity, mixed models) use scipy's quasi-Monte Carlo integration with a fixed seed, accurate to about 6 decimals.
- The confidence interval is difference ± d · SE, where d is the value for which P(max |T| ≤ d) = 95%. All the intervals together have 95% confidence.

**Worked example:** Control A against B and C, three values each.

```example
A: 3, 5, 7
B: 5, 7, 9
C: 9, 11, 13
>>> one_way(table, "ordinary", comparisons="control", control="A", method="dunnett")
comparison_method = dunnett
comparisons[0].difference = -2
comparisons[0].se = 1.633
comparisons[0].df = 6
comparisons[0].statistic = -1.225
comparisons[0].p = 0.2666
comparisons[0].p_adjusted = 0.4179
comparisons[0].ci_low = -6.675
comparisons[0].ci_high = 2.675
comparisons[1].difference = -6
comparisons[1].statistic = -3.674
comparisons[1].p = 0.01040
comparisons[1].p_adjusted = 0.01842
comparisons[1].ci_low = -10.67
comparisons[1].ci_high = -1.325
```

By hand:

- MS_residual = 4 and df = 6 (the pooled SD is 2). SE = √(4 · (1/3 + 1/3)) = 1.633 for both comparisons. The correlation between the two t statistics is 0.5.
- A vs. B: difference 5 − 7 = −2, t = −1.225. A vs. C: 5 − 11 = −6, t = −3.674. The unadjusted P values are 2 · P(T₆ > |t|) = 0.2666 and 0.01040.
- Adjusted P for A vs. C: the chance that the larger of |T₁|, |T₂| reaches 3.674 with 6 df and correlation 0.5 is 0.0184. It is larger than the unadjusted 0.0104, since two chances at a false alarm are being allowed for, and smaller than Bonferroni's 2 · 0.0104 = 0.0208 because Dunnett uses the correlation.
- This probability has no short formula. To check it independently, `scipy.stats.dunnett([5, 7, 9], [9, 11, 13], control=[3, 5, 7]).pvalue` gives 0.418 and 0.018 (it uses random integration, so the third decimal can wobble), and in R `multcomp::glht(fit, linfct = mcp(group = "Dunnett"))`. A simulation of 10 million draws of two correlated t statistics also gives 0.4179 and 0.0184.
- The critical value d for 2 comparisons and 6 df is 2.863 (Dunnett's table gives 2.86), so the half-width is 2.863 · 1.633 = 4.675 and, for A vs. B, the interval is −2 ± 4.675 = −6.675 to 2.675. It is wider than the unadjusted interval (±2.447 · 1.633 = ±3.996).

**Interpreting the results:** Each row is "reference vs. group". The difference is reference − group, so it is negative when the group is higher than the control. Adjusted P is the P for that group against the control, already corrected for all the groups compared with it. In the example only C differs from A (adjusted P = 0.018, interval excluding 0); B does not (adjusted P = 0.42). A group that is not significantly different from the control is not shown to be equal to it.

**Options:** Reference group (and an optional second reference), and for two references **Each reference is its own family** or **Both references as one family** (not available for Dunnett). The method list is the same as in [[Multiple comparisons after ANOVA]]. In Advanced, the pooled SD and the residual df can be set by hand; the sheet lists them under "Changed from the default".

**Checked against:** `scipy.stats.dunnett`; Dunnett's published two-sided 5% critical values (df = ∞ and 10, 1 to 9 groups); simulations of the multivariate t probability with millions of draws. R's `multcomp` was not used.

**References:** Dunnett CW (1955) *J Am Stat Assoc* 50:1096–1121. Dunnett CW (1964) *Biometrics* 20:482–491.

**Learn more:** [Dunnett's test (Wikipedia)](https://en.wikipedia.org/wiki/Dunnett%27s_test), [Multiple comparisons (Handbook of Biological Statistics)](https://www.biostathandbook.com/multiplecomparisons.html).

**Related:** [[Multiple comparisons after ANOVA]], [[Ordinary one-way ANOVA]], [[Family-wise error rate]], [[Adjusted P value]], [[Confidence interval]]

#### Games-Howell and Dunnett's T3

<!-- equations: Multiple comparisons -->

**In short:** Multiple comparisons that do not assume the groups have the same SD. They follow Welch's ANOVA and use a separate SE and df for every pair.

**When to use it:** The group SDs clearly differ, so you chose [[Welch's and Brown-Forsythe ANOVA]]. Games-Howell compares every pair and is better with larger groups. Dunnett's T3 can be used for any set of comparisons and is better with small groups (fewer than about 50 per group). Tamhane's T2 is a third choice, more conservative still. With equal SDs, Tukey or Dunnett (see [[Multiple comparisons after ANOVA]]) use more of the data and are more powerful.

**What is done to your data:** Each group's mean, SD and n are used on their own. No pooled SD is formed. Blank cells are left out. A group needs at least 2 values for its SD.

**How it's calculated:** For the pair i, j:

- Difference = mean_i − mean_j. SE = √(s_i²/n_i + s_j²/n_j).
- Welch's df for that pair:

      df = (s_i²/n_i + s_j²/n_j)² / ((s_i²/n_i)²/(n_i − 1) + (s_j²/n_j)²/(n_j − 1))

- t = difference / SE, and the unadjusted P comes from the t distribution with that df.
- **Games-Howell**: q = √2 · |t|, and P_adj = P(studentized range of k means with that df ≥ q). The 95% interval uses the studentized range critical value / √2 times SE. It is for every pair only.
- **Dunnett's T3**: P_adj = P(largest |t| of m independent comparisons ≥ |t|) on the **studentized maximum modulus** with m (the number of comparisons in the family) and that pair's df. The interval uses the 95% point of the same distribution.
- **Tamhane's T2**: Welch's t test with a Šidák adjustment for m comparisons.

**Worked example:** Three groups with very different SDs (1, 3 and 12.9), first with Games-Howell.

```example
A: 4, 5, 6
B: 3, 6, 9
C: 10, 20, 30, 40
>>> one_way(table, "welch", comparisons="all", method="games_howell")
comparison_method = games_howell
comparisons[0].difference = -1
comparisons[0].se = 1.826
comparisons[0].df = 2.439
comparisons[0].statistic = 0.7746
comparisons[0].p = 0.6299
comparisons[0].p_adjusted = 0.8565
comparisons[0].ci_low = -9.900
comparisons[0].ci_high = 7.900
comparisons[1].difference = -20
comparisons[1].se = 6.481
comparisons[1].df = 3.048
comparisons[1].p_adjusted = 0.1038
```

By hand:

- A: mean 5, s = 1, n = 3. B: mean 6, s = 3, n = 3. SE(A vs. B) = √(1/3 + 9/3) = √3.333 = 1.826. The difference is 5 − 6 = −1.
- df = 3.333² / ((1/3)²/2 + 3²/2) = 11.11 / (0.0556 + 4.5) = 2.439.
- t = −1 / 1.826 = −0.5477, and P = 2 · P(T_2.439 > 0.5477) = 0.6299 (`2 * scipy.stats.t.sf(0.5477, 2.439)`).
- q = √2 · 0.5477 = 0.7746, and P_adj = `scipy.stats.studentized_range.sf(0.7746, 3, 2.439)` = 0.8565 (in R, `ptukey(0.7746, 3, 2.439, lower.tail = FALSE)`).
- The 95% point of the studentized range for 3 means and 2.439 df is `scipy.stats.studentized_range.ppf(0.95, 3, 2.439)` = 6.894. Divided by √2 it is 4.875, and 4.875 · 1.826 = 8.900, so the interval is −1 ± 8.900 = −9.900 to 7.900.

Dunnett's T3 on the same data:

```example
A: 4, 5, 6
B: 3, 6, 9
C: 10, 20, 30, 40
>>> one_way(table, "welch", comparisons="all", method="dunnett_t3")
comparison_method = dunnett_t3
comparisons[0].statistic = -0.5477
comparisons[0].p = 0.6299
comparisons[0].p_adjusted = 0.9211
comparisons[0].ci_low = -10.50
comparisons[0].ci_high = 8.505
comparisons[1].p_adjusted = 0.1211
```

By hand (A vs. B):

- The same t = −0.5477 and df = 2.439. With m = 3 comparisons, the adjusted P is the chance that the largest of 3 independent |t| values reaches 0.5477: 1 − E_u[(2Φ(0.5477 u) − 1)³], where u = √(χ²_df / df). Numerically (`scipy.integrate.quad` over u with the chi density) this is 0.9211.
- The critical value c with 1 − E_u[(2Φ(c u) − 1)³] = 0.05 is 5.206 for df 2.439, so the interval is −1 ± 5.206 · 1.826 = −10.50 to 8.505.

**Interpreting the results:** Read it like any multiple comparison: adjusted P below alpha means the pair differs, and the interval excludes 0. With tiny groups and unequal SDs these tests are cautious: here no pair reaches significance even though C is far above A and B, because C has a very large SD and the groups are small. The df is not a whole number and differs by pair; that is expected.

**Options:** After Welch's ANOVA the method list offers Games-Howell (every pair only; recommended for every pair), Dunnett's T3 (recommended for control or selected pairs), Tamhane's T2, Fisher's LSD (Welch's t, no correction) and the FDR methods. The goals are the same as in [[Multiple comparisons after ANOVA]]; there is no SD or df override for these tests.

**Checked against:** Games-Howell and Tamhane's T2 written out independently in the tests; simulations of the studentized maximum modulus probability.

**References:** Games PA, Howell JF (1976) *J Educ Stat* 1:113–125. Dunnett CW (1980) *J Am Stat Assoc* 75:789–795. Tamhane AC (1979) *J Am Stat Assoc* 74:471–480.

**Learn more:** [Games-Howell test (Wikipedia)](https://en.wikipedia.org/wiki/Games%E2%80%93Howell_test), [Multiple comparisons (Handbook of Biological Statistics)](https://www.biostathandbook.com/multiplecomparisons.html).

**Related:** [[Multiple comparisons after ANOVA]], [[Welch's and Brown-Forsythe ANOVA]], [[Welch's t test]], [[Degrees of freedom]], [[Adjusted P value]]

#### Dunn's test

<!-- equations: Multiple comparisons -->

**In short:** Multiple comparisons after Kruskal-Wallis or Friedman. It compares the groups' mean ranks and corrects the P values for the number of comparisons.

**When to use it:** After [[Kruskal-Wallis test]] (unpaired) or [[Friedman test]] (matched), when you cannot assume Gaussian values and you want to know which groups differ. It compares ranks, so it makes no Gaussian assumption.

**What is done to your data:** All values are ranked together, and tied values get the average of their ranks (Kruskal-Wallis). In the Friedman test, values are ranked within each row (each matched subject). Blank cells are left out. Each group is then summarised by its mean rank. No confidence intervals are given, because a difference of ranks has no natural scale.

**How it's calculated:** With N values in total, mean ranks R̄ᵢ and group sizes nᵢ (Kruskal-Wallis):

- Difference = R̄ᵢ − R̄ⱼ (the "Mean rank diff." in the sheet).
- Variance of a rank difference, with the correction for ties:

      Var = (N(N + 1)/12 − Σ(T³ − T) / (12(N − 1))) · (1/nᵢ + 1/nⱼ)

  where T is the number of values tied at each rank (Σ is over all tied groups; with no ties the sum is 0).
- z = (R̄ᵢ − R̄ⱼ) / √Var. The unadjusted two-tailed P is from the normal distribution: P = 2 · P(Z > |z|).
- Adjusted P = min(1, m · P), with m the number of comparisons in the family (a Bonferroni correction). Large values are shown as >0.9999 (>.999 or >0.999 in the APA and NEJM styles).
- After a Friedman test, SE = √(k(k + 1) / (6n)) for k columns and n rows, with mean ranks from the within-row ranks, and no tie correction.
- **Smallest possible P.** With the group sizes you have, the largest possible mean-rank difference is N − (nᵢ + nⱼ)/2 (Kruskal-Wallis; k − 1 for Friedman). If even that cannot give an adjusted P below alpha, the sheet warns which comparisons can never be significant. Four groups of 2 cannot reach P < 0.05 for any of the 6 comparisons.

**Worked example:** Three groups of four values.

```example
A: 1, 2, 3, 4
B: 3.5, 5, 6, 7
C: 8, 9, 10, 11
>>> one_way(table, "kruskal_wallis", comparisons="all")
comparison_method = dunn
mean_ranks[0] = 2.75
mean_ranks[1] = 6.25
mean_ranks[2] = 10.5
comparisons[0].difference = -3.5
comparisons[0].se = 2.550
comparisons[0].statistic = -1.373
comparisons[0].p = 0.1698
comparisons[0].p_adjusted = 0.5094
comparisons[1].difference = -7.75
comparisons[1].statistic = -3.040
comparisons[1].p = 0.002367
comparisons[1].p_adjusted = 0.007102
comparisons[2].p_adjusted = 0.2866
```

By hand:

- Rank the 12 values: 1, 2, 3, 3.5, 4, 5, 6, 7, 8, 9, 10, 11 get ranks 1 to 12. A = 1, 2, 3, 4 has ranks 1, 2, 3, 5 (sum 11, mean 2.75). B has ranks 4, 6, 7, 8 (sum 25, mean 6.25). C has ranks 9 to 12 (sum 42, mean 10.5). There are no ties.
- N(N + 1)/12 = 12 · 13 / 12 = 13. For two groups of 4, Var = 13 · (1/4 + 1/4) = 6.5 and SE = √6.5 = 2.550.
- A vs. B: difference 2.75 − 6.25 = −3.5, z = −3.5 / 2.550 = −1.373, P = 2 · P(Z > 1.373) = 0.1698 (`2 * scipy.stats.norm.sf(1.373)`; in R `2 * pnorm(1.373, lower.tail = FALSE)`), adjusted P = 3 · 0.1698 = 0.5094.
- A vs. C: difference 2.75 − 10.5 = −7.75, z = −3.040, P = 0.002367, adjusted P = 3 · 0.002367 = 0.007102.
- B vs. C: difference −4.25, z = −1.667, P = 0.09552, adjusted P = 0.2866.

**Interpreting the results:** The sheet gives the mean rank difference, the adjusted P and whether it is significant. Only A and C differ (adjusted P = 0.0071). A mean rank difference is not a difference in medians; with different shapes and spreads it can be significant even when medians match. The comparisons use the same ranks as the Kruskal-Wallis test, so the overall test and Dunn's test agree in what they rank. Because Dunn's correction is plain Bonferroni, it is conservative with many groups.

**Options:** Every pair, each group against one or two references, or selected pairs. The methods are Dunn's test (corrected), the uncorrected Dunn's test (the same z, no correction) and the FDR methods (which start from the uncorrected Dunn P values). In Advanced, the number of comparisons the correction counts can be set by hand (at least the number made).

**Checked against:** Dunn's test written out from its formulas in the independent tests, and R's `kruskal.test` for the overall test (PlantGrowth, InsectSprays, the Hollander and Wolfe examples). Other packages' Dunn's tests may differ in the tie correction or the multiple-comparison adjustment; this engine's choices are the ones listed above.

**References:** Dunn OJ (1964) *Technometrics* 6:241–252.

**Learn more:** [Kruskal-Wallis one-way analysis of variance (Wikipedia)](https://en.wikipedia.org/wiki/Kruskal%E2%80%93Wallis_one-way_analysis_of_variance), [Kruskal-Wallis test (Handbook of Biological Statistics)](https://www.biostathandbook.com/kruskalwallis.html).

**Related:** [[Kruskal-Wallis test]], [[Friedman test]], [[Ranks and ties]], [[Multiple comparisons after ANOVA]], [[Adjusted P value]], [[False discovery rate (q values)]]

#### Test for linear trend

<!-- equations: Test for linear trend -->

**In short:** Asks whether the group means rise or fall steadily along the order of the columns (for example increasing doses), and whether what is left over is more than chance.

**When to use it:** The columns are ordered and roughly equally spaced (dose 0, 1, 2, 3; or time points), and you predicted a trend before looking. It is one planned test, so it needs no multiple comparison correction. It needs three or more groups and equal SDs. It follows an ordinary or repeated-measures ANOVA, not a rank test or Welch's ANOVA. If the columns are not ordered, or not evenly spaced, the trend means little; use ordinary comparisons or [[Linear regression]] with the real X values.

**What is done to your data:** The columns are numbered 1, 2, ..., k in table order; this number is the X of the trend, whatever the column titles say. The group means, sizes and the ANOVA's pooled error term are used. Blanks are left out as in the ANOVA. After a repeated-measures ANOVA with the Geisser-Greenhouse correction on (the default), the trend instead uses the covariance of the columns divided by n, with n − 1 df, and the nonlinear variation test is not given.

**How it's calculated:** With x_i = 1 ... k, group means ȳᵢ, sizes nᵢ, N = Σnᵢ, the weighted mean x̄_w = Σnᵢxᵢ / N, and the contrast cᵢ = nᵢ(xᵢ − x̄_w):

- Slope = Σcᵢȳᵢ / Σnᵢ(xᵢ − x̄_w)², the least-squares slope of the values on column number (the change in the mean per column step).
- SS_trend = (Σcᵢȳᵢ)² / Σnᵢ(xᵢ − x̄_w)², with 1 df.
- F = SS_trend / MS_residual, with (1, residual df). The P value is the upper tail of the F distribution.
- Two R² values:
  - **Effect-size R²** = SS_trend / SS_total: the share of all the variation explained by the trend.
  - **Alerting R²** = SS_trend / SS_between, where SS_between = Σnᵢ(ȳᵢ − ȳ)² is the variation among the group means. It is the share of the differences among means that is a straight line, and it is always the larger of the two.
- **Nonlinear variation**: F = [(SS_between − SS_trend) / (k − 2)] / MS_residual, with (k − 2, residual df). A small P says the means depart from a straight line.

**Worked example:** Three groups with means 5, 7 and 11.

```example
A: 3, 5, 7
B: 5, 7, 9
C: 9, 11, 13
>>> one_way(table, "ordinary", comparisons="trend")
trend_slope = 3
trend_ss = 54
trend_f = 13.5
trend_df = 6
trend_p = 0.01040
trend_r_squared = 0.675
trend_alerting_r_squared = 0.9643
trend_ss_between_means = 56
nonlinear_f = 0.5
nonlinear_p = 0.5060
```

By hand:

- x = 1, 2, 3 with n = 3 each, so x̄_w = 2 and c = 3 · (−1, 0, 1) = (−3, 0, 3). Σcᵢȳᵢ = −3 · 5 + 0 · 7 + 3 · 11 = 18, and Σnᵢ(xᵢ − x̄_w)² = 3 · (1 + 0 + 1) = 6.
- Slope = 18 / 6 = 3. SS_trend = 18² / 6 = 54.
- MS_residual = 4 (SS_residual 24 with 6 df), so F = 54 / 4 = 13.5 with (1, 6) df. P = `scipy.stats.f.sf(13.5, 1, 6)` = 0.01040 (in R `pf(13.5, 1, 6, lower.tail = FALSE)`).
- SS_total = 80 and SS_between = 56, so effect-size R² = 54 / 80 = 0.675 and alerting R² = 54 / 56 = 0.9643.
- Nonlinear: (56 − 54) / (3 − 2) = 2, F = 2 / 4 = 0.5 with (1, 6) df, P = 0.5060.

**Interpreting the results:** A **small P for the trend** is evidence that the means move steadily with column number; the **slope** says by how much per column. It does not say the trend is a straight line: that is the nonlinear variation test, where a large P means there is no sign of curvature. The two R² values answer different questions: the effect-size R² is what part of the whole scatter the trend explains (0.675 here); the alerting R² is how much of the group differences is linear (0.96 here). A trend test can be significant even when pairwise comparisons are not, and the reverse, since it uses all the groups together.

**Options:** Choose "Test for a linear trend across the columns" as the multiple comparisons goal (it is switched off after a rank test or Welch's ANOVA). Alpha and P value style as elsewhere. In Advanced, the pooled SD used by the test can be set by hand.

**Checked against:** The trend test written out from its formulas in the independent tests.

**References:** Maxwell SE, Delaney HD, *Designing Experiments and Analyzing Data*, chapter on trend analysis. Rosenthal R, Rosnow RL, Rubin DB (2000) *Contrasts and Effect Sizes in Behavioral Research* (alerting R²).

**Learn more:** [One-way ANOVA (Handbook of Biological Statistics)](https://www.biostathandbook.com/onewayanova.html), [Linear regression (Wikipedia)](https://en.wikipedia.org/wiki/Linear_regression).

**Related:** [[Ordinary one-way ANOVA]], [[Repeated-measures one-way ANOVA]], [[Multiple comparisons after ANOVA]], [[R squared]], [[F ratio]], [[Linear regression]]

#### False discovery rate (q values)

<!-- equations: FDR control -->

**In short:** Instead of the chance of any false alarm, controls the expected share of false alarms among the results you call discoveries. The q value is the smallest false discovery rate at which a result would still be a discovery.

**When to use it:** You make many comparisons (many genes, many cell features, many pairs) and expect several real effects. Allowing a known small share of false discoveries (5% is common) is better than missing real effects with a strict correction. With only a few planned comparisons, use a family-wise method (see [[Multiple comparisons after ANOVA]]). Choose among three methods:

- **Two-stage step-up of Benjamini, Krieger and Yekutieli (BKY)**: the recommended method. It first estimates how many nulls are true, which makes it more powerful than Benjamini-Hochberg when there are real effects.
- **Benjamini-Hochberg (BH)**: valid for independent or positively dependent tests.
- **Benjamini-Yekutieli (BY)**: valid under any dependence, and more conservative.

**What is done to your data:** After an ANOVA, the FDR methods start from the **unadjusted** P values of every comparison in the family: Fisher's LSD after an ANOVA, Welch's t test after Welch's ANOVA, and uncorrected Dunn's after a rank test. P values pasted into [[Analyze a stack of P values]] are used as given; blanks are left out. The number of tests N is the number of P values in the family.

**How it's calculated:** Sort the N P values ascending, with rank i = 1 for the smallest. Q is the false discovery rate you accept (alpha, 5% by default). All methods step up from the largest P:

- **BH**: the q value of rank r is q₍ᵣ₎ = min over s ≥ r of N · P₍ₛ₎ / s, capped at 1. A comparison is a discovery when q ≤ Q.
- **BY**: q = (BH q) · H, with H = 1 + 1/2 + ... + 1/N, capped at 1.
- **BKY**: stage 1 runs BH at Q′ = Q / (1 + Q) and finds r₁ discoveries. If r₁ = 0 there are no discoveries, and if r₁ = N all are. Otherwise N_true = N − r₁ estimates the number of true nulls, and stage 2 uses the thresholds i · Q / ((1 + Q) · N_true) for rank i.
  - The q value is the Q at which a comparison sits exactly on the border of being a discovery. N_true itself changes with Q, so the engine finds the border interval by interval (r₁ is a step function of Q).
  - This border q is not the same number as statsmodels' `fdr_tsbky` corrected values, which are adjusted at one fixed Q. Discoveries at any Q do match statsmodels.

**Worked example:** Ten P values, Q = 5%, first with Benjamini-Hochberg.

```example
>>> p = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216]
>>> p_value_stack(p, "bh")
m = 10
discoveries = 2
table[0].adjusted = 0.01
table[1].adjusted = 0.04
table[2].adjusted = 0.084
table[5].adjusted = 0.1
table[7].adjusted = 0.216
```

By hand (BH):

- N · P / rank for the ranks 1 to 10 is 0.010, 0.040, 0.130, 0.1025, 0.084, 0.100, 0.1057, 0.2563, 0.2356, 0.216.
- Take the running minimum from the largest rank down: 0.216 for ranks 8 to 10, 0.1057 for rank 7 (0.1057 < 0.216), 0.100 for rank 6, 0.084 for ranks 5, 4 and 3, then 0.040 and 0.010. These are the q values.
- Two q values are ≤ 0.05 (0.010 and 0.040), so there are 2 discoveries at Q = 5%. In statsmodels this is `multipletests(p, method="fdr_bh")`.
- BY multiplies these by H = 1 + 1/2 + ... + 1/10 = 2.929: 0.0293, 0.1172, and so on, so only 1 discovery.

The same P values with the default, two-stage BKY:

```example
>>> p = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216]
>>> p_value_stack(p, "bky")
m0 = 8
discoveries = 2
table[0].adjusted = 0.01010
table[1].adjusted = 0.03734
table[2].adjusted = 0.07204
table[7].adjusted = 0.1111
```

By hand (BKY):

- Stage 1 is BH at Q′ = 0.05 / 1.05 = 0.04762: the thresholds are i · 0.004762, and 0.001 and 0.008 pass (0.008 ≤ 0.009524) but 0.039 does not (0.039 > 0.01429). So r₁ = 2 and N_true = 10 − 2 = 8.
- Stage 2 thresholds are i · 0.05 / (1.05 · 8) = i · 0.005952: 0.001 ≤ 0.00595 and 0.008 ≤ 0.01190 pass, and 0.039 > 0.01786 does not. 2 discoveries, with an estimated 8 true nulls (m0 = 8).
- The q of rank 1 is the Q where stage 1 first lets it pass: the BH q is 0.01 and Q′ = Q/(1 + Q) ≥ 0.01 needs Q = 0.01 / 0.99 = 0.01010. The q of rank 2: while r₁ = 1, stage 2 needs Q/(1 + Q) ≥ 0.04 · 9/10 = 0.036, so Q = 0.036 / 0.964 = 0.03734.

**Interpreting the results:** The sheet gives each comparison's q value and "Discovery? Yes/No". With Q = 5%, you accept that about 5% of the discoveries may be false in the long run, as an average over many such experiments, not for any single result. A q of 0.04 means that if you call this and every result with a smaller q a discovery, about 4% of those discoveries are expected to be false. A q is not a P value, and a q below 0.05 does not mean P < 0.05 for its own comparison (it can be larger or smaller than P). BKY gives its estimate of the true nulls (m0) on the stack sheet. FDR methods give no confidence intervals.

**Options:** The three methods, listed as "FDR: ..." among the comparison methods, and Q (the "alpha" box). For FDR methods the sheet labels alpha as "Q (desired FDR)". The number-of-comparisons override does not apply to them.

**Checked against:** statsmodels `multipletests` for `fdr_bh`, `fdr_by` and `fdr_tsbky` (discoveries at any Q); the BKY thresholds written out by hand in the tests.

**References:** Benjamini Y, Hochberg Y (1995) *J R Stat Soc B* 57:289–300. Benjamini Y, Yekutieli D (2001) *Ann Stat* 29:1165–1188. Benjamini Y, Krieger AM, Yekutieli D (2006) *Biometrika* 93:491–507.

**Learn more:** [False discovery rate (Wikipedia)](https://en.wikipedia.org/wiki/False_discovery_rate), [Multiple comparisons: false discovery rate (Handbook of Biological Statistics)](https://www.biostathandbook.com/multiplecomparisons.html), [StatQuest video index](https://statquest.org/video-index/).

**Related:** [[Analyze a stack of P values]], [[Multiple comparisons after ANOVA]], [[Adjusted P value]], [[Family-wise error rate]], [[P value]]

#### Compact letter display

<!-- equations: Compact letter display -->

**In short:** Puts letters above the groups so that groups sharing a letter are not significantly different.

**When to use it:** You compared every pair of groups and want a tidy summary, for example above the bars of a graph. It needs the results of a multiple comparison of every pair (Tukey, Games-Howell, Šidák and so on); it is offered for every-pair comparisons only.

**What is done to your data:** Nothing is recomputed. The letters come only from the "Significant?" column of the comparisons: a pair is different when its adjusted P is below alpha (for FDR methods, when q ≤ Q). A pair that was not compared counts as not different.

**How it's calculated:** The insert-absorb algorithm of Piepho (2004):

1. Start with one letter that every group holds.
2. For each significant pair, split every letter that holds both groups into two letters: one without the first group, one without the second.
3. Drop any letter whose set of groups is contained in another letter's set.
4. Name the letters in column order. Every group ends up with at least one letter. The order of the letters is this engine's choice, so another package may use a different but equivalent lettering.

Two groups share a letter exactly when they are not significantly different. Styles: upper case A B C (the default), lower case a b c, or numbers 1 2 3 (joined with commas when a group has several).

**Worked example:** The three groups compared with Tukey's test (only A vs. C is significant).

```example
Ctrl: 3, 5, 7
Low: 5, 7, 9
High: 9, 11, 13
>>> from types import SimpleNamespace
>>> res = one_way(table, "ordinary", comparisons="all", method="tukey")
>>> SimpleNamespace(values=compact_letter_display(res))
Ctrl = A
Low = AB
High = B
```

By hand:

- Tukey's adjusted P values are 0.4827 (Ctrl vs. Low), 0.0242 (Ctrl vs. High) and 0.1089 (Low vs. High). Only Ctrl vs. High is below 0.05.
- Start with one letter {Ctrl, Low, High}. The significant pair Ctrl and High splits it into {Low, High} and {Ctrl, Low}.
- Name them in column order: {Ctrl, Low} = A, {Low, High} = B. So Ctrl = A, Low = AB, High = B.
- Ctrl and High share no letter, so they differ. Ctrl and Low share A, and Low and High share B, so those pairs are not different.

**Interpreting the results:** Groups with a common letter are not significantly different; groups with no letter in common are. The middle group "AB" does not differ from either neighbour, which does not make it a separate class. Letters inherit the weaknesses of the comparisons behind them: "not different" is not "the same", and the letters change if you change the method or alpha. Letters from comparisons against a control, selected pairs or a trend test make no sense, so the option is not offered.

**Options:** On a graph linked to an every-pair comparison, choose **Compact letters above columns** in the format panel, and the letter style (A B C, a b c or 1 2 3). The alpha is the one of the comparison; in code, `alpha=` recomputes significance from the adjusted P (or q).

**Checked against:** For PlantGrowth with Tukey's test the letters are ctrl "AB", trt1 "A", trt2 "B". The letter order is not checked against other packages.

**References:** Piepho HP (2004) *J Comput Graph Stat* 13:456–466.

**Learn more:** [Multiple comparisons (Handbook of Biological Statistics)](https://www.biostathandbook.com/multiplecomparisons.html), [Tukey's range test (Wikipedia)](https://en.wikipedia.org/wiki/Tukey%27s_range_test).

**Related:** [[Multiple comparisons after ANOVA]], [[Adjusted P value]], [[Alpha]], [[Ordinary one-way ANOVA]]

#### Analyze a stack of P values

<!-- opens: pstack -->
<!-- equations: Analyze a stack of P values -->

**In short:** Corrects a list of P values you already have (from any source) for the number of tests, giving adjusted P values or q values.

**When to use it:** You have P values from many separate tests, for example one t test per gene or per feature, and need to correct them for multiple testing. The tests can come from anywhere; you only paste their P values. Use a family-wise method (Bonferroni, Šidák, Holm, Holm-Šidák) when any false positive is costly. Use an FDR method (see [[False discovery rate (q values)]]) when many tests are made and a small share of false discoveries is acceptable.

**What is done to your data:** Paste one P value per line, with an optional label before it (for example `Gene 3  0.21`). Blank lines and blank entries are left out and counted in a warning. Every P value must be between 0 and 1. Only P values enter; nothing is known of the tests behind them, so the correction assumes that you paste every test you ran in the family, including the non-significant ones. Leaving out the large P values makes the correction too small.

**How it's calculated:** With m P values, rank i = 1 for the smallest, and alpha the family-wise alpha (or Q for FDR):

- **Bonferroni**: P_adj = min(1, m · P).
- **Šidák**: P_adj = 1 − (1 − P)^m.
- **Holm** (Bonferroni step-down): with the P values sorted ascending, P_adj(r) = largest, over s ≤ r, of min(1, (m − s + 1) · P(s)).
- **Holm-Šidák**: the same, with 1 − (1 − P(s))^(m − s + 1) in place of (m − s + 1) · P(s).
- **BKY, BH, BY**: q values, as explained in [[False discovery rate (q values)]].
- A P value is significant when its adjusted P is below alpha, and a discovery when q ≤ Q. For the two-stage BKY method the sheet also reports the estimated number of true nulls (m0).

**Worked example:** The ten P values of the FDR page, corrected with Holm's method at alpha = 0.05.

```example
>>> p = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216]
>>> p_value_stack(p, "holm")
m = 10
discoveries = 1
table[0].rank = 1
table[0].adjusted = 0.01
table[1].adjusted = 0.072
table[2].adjusted = 0.312
table[3].adjusted = 0.312
table[7].adjusted = 0.615
table[9].adjusted = 0.615
```

By hand:

- The factors for ranks 1 to 10 are 10, 9, 8, ..., 1. Multiplying: 10 · 0.001 = 0.010, 9 · 0.008 = 0.072, 8 · 0.039 = 0.312, 7 · 0.041 = 0.287, 6 · 0.042 = 0.252, 5 · 0.06 = 0.30, 4 · 0.074 = 0.296, 3 · 0.205 = 0.615, 2 · 0.212 = 0.424, 1 · 0.216 = 0.216.
- Holm keeps the running maximum from the smallest P, so a value cannot be smaller than the one before it: 0.010, 0.072, 0.312, 0.312, 0.312, 0.312, 0.312, 0.615, 0.615, 0.615.
- Only the first is below 0.05, so there is 1 significant result. Bonferroni gives 0.01, 0.08, 0.39, and so on (1 significant); BH gives 2 discoveries at Q = 5% (see the FDR page).

**Interpreting the results:** The sheet lists a "Method" section (approach, alpha or Q, number of P values), then one line per P value with its P, its adjusted P (or q) and "Significant?" or "Discovery?", and a "Summary" with the number found. Adjusted P values compare directly with alpha. The stars use the chosen P value style, applied to the adjusted P. The result keeps the order of your list.

**Options:** The correction (the default and recommended one is the two-stage BKY), alpha (called Q for FDR methods), and the P value style. The dialog does not offer "no correction".

**Checked against:** statsmodels `multipletests` (Šidák, Bonferroni, Holm, Holm-Šidák, `fdr_bh`, `fdr_by`, `fdr_tsbky`).

**References:** Holm S (1979) *Scand J Stat* 6:65–70. Šidák Z (1967) *J Am Stat Assoc* 62:626–633. Benjamini Y, Hochberg Y (1995) *J R Stat Soc B* 57:289–300.

**Learn more:** [Holm-Bonferroni method (Wikipedia)](https://en.wikipedia.org/wiki/Holm%E2%80%93Bonferroni_method), [Bonferroni correction (Wikipedia)](https://en.wikipedia.org/wiki/Bonferroni_correction), [Multiple comparisons (Handbook of Biological Statistics)](https://www.biostathandbook.com/multiplecomparisons.html).

**Related:** [[False discovery rate (q values)]], [[Multiple comparisons after ANOVA]], [[Adjusted P value]], [[Family-wise error rate]], [[P value]]

### Replicate tables

#### Nested t test and nested one-way ANOVA

<!-- opens: nested -->
<!-- equations: Nested t test and nested one-way ANOVA -->

**In short:** Compares groups when every biological replicate contributes many values (for example many cells per experiment). It keeps all the values, but tests the groups against the variation among replicates, so many cells from few replicates do not make P falsely small.

**When to use it:** A [[SuperPlot]]-style table: each column is a group, and each replicate (an animal, a culture, an experiment) has several values in it. Two columns give a nested t test; three or more give a nested one-way ANOVA with [[Multiple comparisons after ANOVA|multiple comparisons]]. If you only want to compare one value per replicate, the ordinary [[Unpaired t test]] or [[Ordinary one-way ANOVA]] on the replicate means gives the same answer when every replicate has the same number of values. If each replicate is measured in every group (a paired design), nested analysis is refused: untick Paired, or use the paired analyses of the replicate means.

**What is done to your data:** Every value that has a replicate name is used. Blank cells are left out. Values above the first replicate name belong to no replicate and are left out (a warning counts them). Nothing is averaged, transformed or removed. A replicate belongs to one group only, even if you type the same name in two columns (Exp1 in Control and Exp1 in Treated are two different replicates). The number of replicates is N and the number of values is n (see [[N and n]]); the groups are compared with N, not n. It is an error if no group has two replicates, or if every replicate has only one value (nothing is nested).

**How it's calculated:** Let k be the number of groups, R the number of replicates in all, N_total the number of values in all, n_gr the number of values in replicate r of group g, and n_g the number of values in group g. ȳ_gr is the mean of a replicate, ȳ_g the mean of all values of a group, ȳ the mean of every value.

- The sums of squares ([[Sum of squares]]):

      SS_groups     = Σ_g n_g (ȳ_g − ȳ)²              df = k − 1
      SS_replicates = Σ_g Σ_r n_gr (ȳ_gr − ȳ_g)²      df = R − k
      SS_residual   = Σ (y − ȳ_gr)²                   df = N_total − R

  Each [[Mean square]] is SS/df.
- The test of the replicates: F = MS_replicates / MS_residual, with (R − k, N_total − R) df. A small P says replicates differ by more than the values inside them do.
- The test of the groups: F = MS_groups / MS_den with (k − 1, df_den) df. When every replicate has the same number of values m, MS_den is MS_replicates and df_den = R − k. This is the ordinary ANOVA on the replicate means. When the numbers differ, MS_den is a mean square built from MS_replicates and MS_residual so that it has the same expectation as MS_groups when there is no group effect, with Satterthwaite's df (not a whole number). A note on the sheet says so.
- The [[Variance components]]: σ²_residual = MS_residual and σ²_replicates = max(0, (MS_replicates − MS_residual) / c_r), where c_r = m for equal numbers (and a coefficient worked out from the n_gr otherwise). The [[Intraclass correlation]] is ICC = σ²_replicates / (σ²_replicates + σ²_residual), the share of the variation that lies among replicates.
- Two groups (nested t test): the difference is the difference of the group means, ȳ_2 − ȳ_1, with SE² = Var(ȳ_1) + Var(ȳ_2). Each group mean has variance σ²_replicates · Σ_r n_gr² / n_g² + MS_residual / n_g (the replicate component here is not clipped at 0, so that t² equals the F of the groups). t = difference / SE, with df_den. The two-tailed [[P value]] is 2 · P(T > |t|). The [[Confidence interval]] is the difference ± t* · SE, with t* from the t distribution with df_den.
- Three or more groups: the multiple comparisons use the group means, the variances above and df_den, with the same procedures as after an ordinary ANOVA (the test for linear trend is not offered).

**Worked example:** Two groups, two replicates each, two values per replicate.

```example
>>> from cellquant.stats.replicates import ReplicateTable
>>> table = ReplicateTable("Intensity", ["Control", "Treated"], [[2, 4, 6, 8], [8, 10, 12, 14]], ["Exp1", "", "Exp2", ""])
>>> nested(table)
means[0] = 5
means[1] = 11
f = 4.5
dfn = 1
dfd = 2
p = 0.1679
f_replicates = 8
p_replicates = 0.04
var_replicates = 7
var_residual = 2
icc = 0.7778
difference = 6
se = 2.828
t = 2.121
df = 2
ci_low = -6.170
ci_high = 18.17
```

By hand:

- Replicate means: Control Exp1 = 3, Exp2 = 7; Treated Exp1 = 9, Exp2 = 13. Group means 5 and 11; grand mean 8.
- SS_groups = 4·(5 − 8)² + 4·(11 − 8)² = 72, df 1, MS = 72.
- SS_replicates = 2·(3 − 5)² + 2·(7 − 5)² + 2·(9 − 11)² + 2·(13 − 11)² = 32, df 4 − 2 = 2, MS = 16.
- SS_residual: each replicate has two values 2 apart, so (±1)² twice = 2; four replicates give 8, df 8 − 4 = 4, MS = 2. The total is 72 + 32 + 8 = 112, df 7.
- Every replicate has m = 2 values, so the groups are tested against MS_replicates: F = 72 / 16 = 4.5 with (1, 2) df. P = `scipy.stats.f.sf(4.5, 1, 2)` = 0.1679 (R: `pf(4.5, 1, 2, lower.tail = FALSE)`). Test of the replicates: F = 16 / 2 = 8 with (2, 4) df, P = 0.0400.
- σ²_replicates = (16 − 2) / 2 = 7 and σ²_residual = 2, so ICC = 7 / 9 = 0.7778.
- Var(group mean) = 7 · (2² + 2²) / 4² + 2 / 4 = 3.5 + 0.5 = 4, so SE = √(4 + 4) = 2.828. t = 6 / 2.828 = 2.121, and t² = 4.5 = F. df = 2, so P = 2 · `scipy.stats.t.sf(2.121, 2)` = 0.1679.
- t* for 95% and 2 df is 4.303, so the CI is 6 ± 4.303 · 2.828 = −6.170 to 18.17.
- Check: the ordinary unpaired t test on the four replicate means (3, 7 against 9, 13) gives the same t, df and P.

**Interpreting the results:** The **P value** (nested t test) or **F and P of the groups** (nested ANOVA) answer the biological question: do the group means differ, judged against replicate-to-replicate variation? Here P = 0.17 with only 2 replicates per group, so there is little power; the CI of the difference (−6.2 to 18.2) is wide for the same reason. The **test of the replicates** (P = 0.04) says the replicates differ by more than their own values do, which is why it would be wrong to treat all 8 values as independent. The **intraclass correlation** (77.8%) is the share of the variation that lies among replicates: the closer it is to 100%, the less an extra cell adds and the more an extra replicate is worth. If the sheet says the replicate variance was set to 0, the replicates varied less than the values within them. The sheet warns when a group has one replicate or when replicates minus groups is below 3; the remedy is more biological replicates, not more cells.

**Options:** One- or two-tailed P (two columns; choose one-tailed only if you predicted the direction), multiple comparisons for three or more columns (every pair, each against one or two reference groups, or selected pairs, with their methods), alpha, the confidence level and the P value style. In Advanced, the denominator df (Default, Conservative = replicates minus groups or the computed df if smaller, or a number set by hand) and, for a Bonferroni-type correction, the number of comparisons counted. Changes are listed under "Changed from the default".

**Checked against:** The ordinary tests on replicate means (equal numbers of values per replicate) and hand-computed expected mean squares, as in docs/STATISTICS.md.

**References:** Sokal RR, Rohlf FJ, *Biometry*, chapter on nested ANOVA. Lord SJ, Velle KB, Mullins RD, Fritz-Laylin LK (2020) SuperPlots. *J Cell Biol* 219:e202001064.

**Learn more:** [Nested ANOVA (Handbook of Biological Statistics)](https://www.biostathandbook.com/nestedanova.html), [Pseudoreplication (Wikipedia)](https://en.wikipedia.org/wiki/Pseudoreplication), [Intraclass correlation (Wikipedia)](https://en.wikipedia.org/wiki/Intraclass_correlation).

**Related:** [[SuperPlot]], [[Biological replicate]], [[Technical replicate]], [[Pseudoreplication]], [[Intraclass correlation]], [[Variance components]], [[Fixed and random effects]], [[Mixed-effects model]], [[Degrees of freedom]], [[N and n]]

#### SuperPlot

<!-- equations: Making a SuperPlot -->

**In short:** A graph that shows every value (each cell or image) as a small dot and each biological replicate as a large dot in the same colour, with the mean and spread of the replicates. The statistics beside it use one value per replicate.

**When to use it:** Whenever each biological replicate gives many measurements (cells, images, fields). It shows how much the measurements vary inside a replicate and how much the replicates differ from each other, so a reader can judge whether a difference is repeatable. The tests that go with it are the ordinary tests on replicate values (for example the [[Unpaired t test]] or [[Paired t test]]) or the [[Nested t test and nested one-way ANOVA|nested analyses]], which keep every value.

**What is done to your data:** The graph is not a results sheet, so it does not have its own sheet. It needs a table with replicates: a Replicate column with one name per block of rows, the same names in every column; a blank name continues the replicate above (or a CSV with a replicate column, or CellQuant results imported with Dot = biological replicate). Values above the first replicate name belong to no replicate and are left out. For each column and each replicate, the value of the replicate is the **mean** of its values in that column (or the **median**, if you choose Median in the Replicate mean menu). Blanks are ignored. A replicate with no value in a column has no dot there.

The analyses of such a table (t tests, ANOVA, correlation and so on) are run on those replicate values, one per replicate and column, so N is the number of replicates. Each replicate counts once however many values it has, so keep the numbers of values per replicate similar: a mean of 3 cells is trusted as much as a mean of 300. The results sheet says so in its methods ("Analyzed on one value per biological replicate (each replicate's mean of its values), not on the individual values", with N and n for each column). Tick **Paired** when every replicate was measured in every column: paired tests and repeated-measures ANOVA then compare each replicate with itself. The nested analyses are the exception: they use every value.

**How it's calculated:** For replicate r in column c with values y_1 … y_m:

- Replicate value (mean option) = (y_1 + … + y_m) / m. Replicate value (median option) = the middle value of y_1 … y_m.
- N for the column = the number of replicates that have at least one value; n = the number of values that belong to a replicate.
- The graph's large markers are the replicate values; the bar or line and its error bars are the mean and SD, SEM or CI of those replicate values (not of all the values).

**Worked example:** One column with two replicates; Exp1 has three values, Exp2 has two.

```example
>>> from cellquant.stats.replicates import ReplicateTable
>>> table = ReplicateTable("Intensity", ["Control"], [[2, 4, 9, 6, 8]], ["Exp1", "", "", "Exp2", ""])
>>> by_mean = table.summaries()["value"].tolist()
>>> table.summary = "median"
>>> result = AnalysisResult("Replicate values", "Intensity")
>>> result.values.update(mean=by_mean, median=table.summaries()["value"].tolist(), mean_of_replicates=float(np.mean(by_mean)), mean_of_all_values=float(np.mean(table.values[0])))
>>> result
mean[0] = 5
mean[1] = 7
median[0] = 4
median[1] = 7
mean_of_replicates = 6
mean_of_all_values = 5.8
```

By hand:

- Exp1 = (2 + 4 + 9) / 3 = 5 and Exp2 = (6 + 8) / 2 = 7, so the column is analyzed as the two numbers 5 and 7: N = 2, n = 5.
- The mean of the replicates is (5 + 7) / 2 = 6, while the mean of all five values is 29 / 5 = 5.8. They differ because Exp1 has more values; the analysis uses 6.
- With the median option, Exp1 = 4 (the middle of 2, 4, 9) and Exp2 = 7 (the median of two values is their mean). A median is less moved by an odd cell such as the 9.

**Interpreting the results:** Small dots that overlap between groups but large dots that separate consistently show a repeatable difference. Large dots that scatter widely while the small dots form tight clusters show that replicates differ more than their cells do, which is why the statistics must use the replicates. Error bars of the replicates (SEM or CI) show how repeatable the experiment is; SD shows how much the replicates vary.

**Options:** In the Graph section of the format panel: colours for replicates (bold, colour-blind safe or greys), a different symbol for each replicate, small dots together in one swarm or in a lane for each replicate, the opacity of the small dots, and Join each replicate (a line joining a replicate's markers across columns, for paired designs). Error bars and markers work as on other graphs and describe the replicates.

**References:** Lord SJ, Velle KB, Mullins RD, Fritz-Laylin LK (2020) SuperPlots: communicating reproducibility and variability in cell biology. *J Cell Biol* 219:e202001064.

**Learn more:** [Pseudoreplication (Wikipedia)](https://en.wikipedia.org/wiki/Pseudoreplication).

**Related:** [[Nested t test and nested one-way ANOVA]], [[Biological replicate]], [[Technical replicate]], [[Pseudoreplication]], [[N and n]], [[Mean]], [[Median]], [[Paired data]]

### Grouped tables

#### Two-way ANOVA

<!-- opens: twoway -->
<!-- equations: Two-way ANOVA -->

**In short:** Asks whether two factors (for example treatment and genotype) each change the mean, and whether the effect of one depends on the other (the interaction).

**When to use it:** Values classified by two grouping factors, such as treatment (rows) by genotype (data sets), with the replicates of each combination side by side. If there is one factor only, use the [[Ordinary one-way ANOVA]]. Values should be roughly Gaussian with similar spread in every cell, and independent: each value should be a biological replicate (see [[N and n]]). Many cells from one animal are not independent; a warning appears when a cell has more than 30 values.

**What is done to your data:** A grouped table has one row per level of the row factor, one data set per level of the column factor, and each cell's replicates side by side (`GroupedTable.from_cells` builds one from `{(row, data set): values}`). Blank cells are left out. Cells may have different numbers of values. Nothing is transformed or removed. Every row and data set needs at least one value, and some cell needs at least 2 values. With an empty cell the interaction cannot be fitted: untick it (additive model) or fill the cell. If every cell has one value, the interaction is dropped automatically and the sheet says so.

**How it's calculated:** For row level i, column level j and replicate k, the model is

    y_ijk = μ + α_i + β_j + (αβ)_ij + ε_ijk

fitted by least squares ([[Least squares]]) with sum-to-zero coding. With a rows, b data sets and n values in every cell (a balanced table):

- Row factor: SS_row = b · n · Σ_i (ȳ_i·· − ȳ)², df = a − 1.
- Column factor: SS_column = a · n · Σ_j (ȳ_·j· − ȳ)², df = b − 1.
- Interaction: SS_interaction = n · Σ_ij (ȳ_ij· − ȳ_i·· − ȳ_·j· + ȳ)², df = (a − 1)(b − 1).
- Residual: SS_residual = Σ (y − ȳ_ij·)², df = N − a·b.
- Each [[Mean square]] is SS / df, each [[F ratio]] is MS_effect / MS_residual, and P is the upper tail of the F distribution with (df_effect, df_residual) df. "% of total variation" is 100 · SS_effect / SS_total.
- With unequal numbers of values per cell, each effect's SS is the rise in the residual SS when that effect alone is left out of the full model (Type III), so each effect is adjusted for the others and the SS no longer add up to the total.
- Without the interaction the model is y = μ + α_i + β_j + ε.
- Multiple comparisons, if chosen, use the model's cell means (or, for a main effect, least-squares means that weight every cell equally), the pooled residual mean square and its df.

**Worked example:** Two treatments (rows A and B), two genotypes (X and Y), two replicates per cell.

```example
>>> table = GroupedTable.from_cells({("A", "X"): [2, 4], ("A", "Y"): [6, 8], ("B", "X"): [8, 10], ("B", "Y"): [8, 10]})
>>> two_way_anova(table)
anova[0].ss = 8
anova[0].df = 1
anova[0].f = 4
anova[0].p = 0.1161
anova[1].ss = 32
anova[1].f = 16
anova[1].p = 0.01613
anova[2].ss = 8
anova[2].f = 4
anova[2].p = 0.1161
ss_resid = 8
df_resid = 4
ms_resid = 2
ss_total = 56
```

The list `anova` holds the interaction first, then the row factor, then the column factor.

By hand:

- Cell means: A/X = 3, A/Y = 7, B/X = 9, B/Y = 9. Row means: A = 5, B = 9. Column means: X = 6, Y = 8. Grand mean 7.
- SS_row = 2 · 2 · ((5 − 7)² + (9 − 7)²) = 4 · 8 = 32, df 1.
- SS_column = 2 · 2 · ((6 − 7)² + (8 − 7)²) = 4 · 2 = 8, df 1.
- The interaction term of each cell, ȳ_ij − ȳ_i − ȳ_j + ȳ: A/X = 3 − 5 − 6 + 7 = −1, A/Y = 7 − 5 − 8 + 7 = 1, B/X = 9 − 9 − 6 + 7 = 1, B/Y = 9 − 9 − 8 + 7 = −1. SS_interaction = 2 · (1 + 1 + 1 + 1) = 8, df 1.
- Residual: each cell's two values are 1 either side of the cell mean, so each cell contributes 1 + 1 = 2; four cells give 8, df = 8 − 4 = 4, MS = 2.
- Total: 32 + 8 + 8 + 8 = 56 = Σ(y − 7)², df 7.
- F_row = 32 / 2 = 16, P = `scipy.stats.f.sf(16, 1, 4)` = 0.01613 (R: `pf(16, 1, 4, lower.tail = FALSE)`). F_column = F_interaction = 8 / 2 = 4, P = `scipy.stats.f.sf(4, 1, 4)` = 0.1161.
- % of total variation: row 32/56 = 57.14%, column 14.29%, interaction 14.29%, residual 14.29%.

**Interpreting the results:** Each effect has its own F and P. A small P for the **row factor** means the row means differ when averaged over data sets; a small P for the **column factor** means the same for data sets. A small P for the **interaction** means the effect of one factor depends on the level of the other (here, A and B differ by 6 in genotype X but by only 2 in genotype Y). When the interaction is large, the main effects average over effects that differ, so interpret simple effects (comparisons within a row or a data set) instead. In the example only the row factor is significant (P = 0.016), with 57% of the variation; the interaction (P = 0.12) is not, with just 2 replicates per cell. "% of total variation" shows how much each source contributes; only for balanced tables do the four values add to 100%.

**Options:** Fit the interaction or not. Multiple comparisons: none; within each row, compare the data sets; within each data set, compare the rows; or compare the main column or row means. Every pair or each with a reference (control); a family for each row (or data set), which allows Tukey (every pair) and Dunnett (against a reference), or all comparisons in one family, which allows Šidák, Holm-Šidák, Bonferroni, Fisher's LSD or an FDR method and is stricter. Alpha, the confidence level and the P value style. In Advanced, the residual df can be set by hand (it replaces the residual df in every F test and in the comparisons); the sheet lists it under "Changed from the default".

**Checked against:** An independent least-squares fit (Type III sums of squares with sum-to-zero contrasts), as in docs/STATISTICS.md.

**References:** Sokal RR, Rohlf FJ, *Biometry*. Zar JH, *Biostatistical Analysis*, chapter 14.

**Learn more:** [Two-way ANOVA (Handbook of Biological Statistics)](https://www.biostathandbook.com/twowayanova.html), [Two-way analysis of variance (Wikipedia)](https://en.wikipedia.org/wiki/Two-way_analysis_of_variance).

**Related:** [[Ordinary one-way ANOVA]], [[Multiple comparisons after ANOVA]], [[Interaction]], [[Main effect]], [[Sum of squares]], [[Mean square]], [[F ratio]], [[Residual]], [[Degrees of freedom]], [[Least squares]]

### XY tables

#### Linear regression

<!-- opens: linreg -->
<!-- equations: Linear regression -->

**In short:** Fits the straight line Y = slope · X + intercept that has the smallest total squared vertical distance to the points, and tests whether the slope differs from zero.

**When to use it:** A numeric X (a dose, a time, a concentration) and a numeric Y that you expect to change roughly in a straight line. It describes how Y depends on X and lets you predict Y from X. If you only want to know how closely two measured variables go together, without a line, use [[Correlation (Pearson and Spearman)]]. If the relationship curves, use [[Nonlinear regression (curve fit)]].

**What is done to your data:** Each data set (Y column) is fitted on its own against the X column. By default every replicate is one point; you can choose instead to use only the mean of each row's replicates as one point. A row without an X is left out, and blank Y cells are left out. Nothing is transformed or removed. A line needs at least 3 points (2 if forced through the origin) and at least two different X values. Points should be independent: replicates of one biological sample are not (see [[N and n]]), so average them with "Only the mean of each row" when they are technical replicates.

**How it's calculated:** With n points, x̄ and ȳ the means, and S_xx = Σ(x − x̄)²:

- Slope a = Σ(x − x̄)(y − ȳ) / S_xx. Intercept b = ȳ − a·x̄. The X-intercept is −b/a.
- The residuals are y − (a·x + b). SS_residual is their sum of squares and SS_total = Σ(y − ȳ)² ([[Sum of squares]]).
- df = n − 2 ([[Degrees of freedom]]). MS_residual = SS_residual / df, and Sy.x = √MS_residual, the typical distance of a point from the line.
- SE(a) = Sy.x / √S_xx and SE(b) = Sy.x · √(1/n + x̄²/S_xx) ([[Standard error]]).
- The CI of each is estimate ± t* · SE, with t* the 97.5th percentile (for 95%) of the t distribution with df degrees of freedom.
- R² = 1 − SS_residual / SS_total ([[R squared]]).
- Is the slope different from zero? F = (SS_total − SS_residual) / MS_residual with (1, df) df. P is the upper tail of the F distribution; it equals the P of the t test of the slope, t = a / SE(a), because F = t².
- Forced through the origin: b = 0, a = Σxy / Σx², df = n − 1, and SS_total is Σy² instead of Σ(y − ȳ)², so this R² is not comparable with an ordinary R².
- Two or more data sets: an F test compares one line per data set with lines that share one slope (analysis of covariance). If the slopes are not significantly different, a second F test compares parallel lines with one line for everything (the intercepts); if the slopes differ, the sheet says the intercepts were not compared.

**Worked example:** Five points.

```example
>>> table = XYTable("Response", "Dose", [1, 2, 3, 4, 5], ["Response"], np.array([2, 4, 5, 4, 5], float).reshape(5, 1, 1))
>>> linear_regression(table)
fits.Response.slope = 0.6
fits.Response.intercept = 2.2
fits.Response.sy_x = 0.8944
fits.Response.se_slope = 0.2828
fits.Response.se_intercept = 0.9381
fits.Response.slope_ci[0] = -0.3001
fits.Response.slope_ci[1] = 1.500
fits.Response.x_intercept = -3.667
fits.Response.r2 = 0.6
fits.Response.f = 4.5
fits.Response.df = 3
fits.Response.p = 0.1240
```

By hand:

- x̄ = 3, ȳ = 4. Deviations of x: −2, −1, 0, 1, 2, so S_xx = 10. Deviations of y: −2, 0, 1, 0, 1, so SS_total = 4 + 0 + 1 + 0 + 1 = 6.
- Σ(x − x̄)(y − ȳ) = 4 + 0 + 0 + 0 + 2 = 6, so slope = 6 / 10 = 0.6 and intercept = 4 − 0.6 · 3 = 2.2. X-intercept = −2.2 / 0.6 = −3.667.
- Fitted values 2.8, 3.4, 4.0, 4.6, 5.2; residuals −0.8, 0.6, 1.0, −0.6, −0.2; SS_residual = 0.64 + 0.36 + 1 + 0.36 + 0.04 = 2.4. df = 3, MS = 0.8, Sy.x = 0.8944.
- SE(slope) = √(0.8 / 10) = 0.2828. SE(intercept) = √(0.8 · (1/5 + 9/10)) = √0.88 = 0.9381.
- R² = 1 − 2.4 / 6 = 0.6. F = (6 − 2.4) / 0.8 = 4.5, with (1, 3) df.
- P = `scipy.stats.f.sf(4.5, 1, 3)` = 0.1240 (R: `pf(4.5, 1, 3, lower.tail = FALSE)`). Equivalently t = 0.6 / 0.2828 = 2.121 and P = 2 · `scipy.stats.t.sf(2.121, 3)`.
- t* for 95% and 3 df is 3.182, so the slope CI is 0.6 ± 3.182 · 0.2828 = −0.3001 to 1.500. The intercept CI is 2.2 ± 3.182 · 0.9381 = −0.7854 to 5.185.

**Interpreting the results:** The **slope** is the change in Y for one unit of X, with its standard error and CI. If the CI of the slope includes 0, the data are compatible with no relationship, as here (P = 0.12, with only 5 points). The **Y-intercept** is the fitted Y at X = 0; it is only meaningful if X = 0 is inside or near your data. **R squared** is the fraction of the variation in Y that the line accounts for (0.6 here); **Sy.x** is the typical scatter around the line in Y units. A small P in "Is the slope different from zero?" says the line is better than a flat line at the mean. A significant slope does not show that X causes Y, and a line can fit badly (a clear curve in the residuals) even with a high R². The sheet warns when a data set has fewer than 5 points.

**Options:** Force the line through X = 0, Y = 0; use each replicate as a point or only the mean of each row; the confidence level, alpha and the P value style. In Advanced, the residual df can be set by hand; it replaces n − 2 in the CIs, the P values and the F test of the slope (the line, SEs and R² are unchanged), and the sheet lists it under "Changed from the default".

**Checked against:** `scipy.stats.linregress`; statsmodels `OLS` (with and without the intercept) for standard errors and the confidence and prediction bands, and its F tests of nested models for comparing slopes and intercepts.

**References:** Zar JH, *Biostatistical Analysis*, chapters 17 and 18. Draper NR, Smith H, *Applied Regression Analysis*.

**Learn more:** [Linear regression and correlation (Handbook of Biological Statistics)](https://www.biostathandbook.com/linearregression.html), [Simple linear regression (Wikipedia)](https://en.wikipedia.org/wiki/Simple_linear_regression), [Ordinary least squares (Wikipedia)](https://en.wikipedia.org/wiki/Ordinary_least_squares).

**Related:** [[Correlation (Pearson and Spearman)]], [[Nonlinear regression (curve fit)]], [[Least squares]], [[R squared]], [[Residual]], [[Standard error]], [[Confidence interval]], [[F ratio]]

#### Correlation (Pearson and Spearman)

<!-- opens: xycorr:pearson, xycorr:spearman, xycorr -->
<!-- equations: Correlation -->

**In short:** Measures how closely X and Y rise and fall together, as a number from −1 to +1. Pearson r measures a straight-line relationship; Spearman rs measures any steady rise or fall, using ranks.

**When to use it:** Two variables that were both measured (neither set by you) and you want the strength of their association, not a line. Use Pearson when both are roughly Gaussian and the relationship is a straight line. Use Spearman for skewed data, outliers, or a steady but curved relationship. For a slope or predictions use [[Linear regression]]; correlation does not say which variable depends on the other.

**What is done to your data:** X is correlated with each data set's Y on its own. By default every replicate is a point (or choose only each row's mean). Rows without X and blank Y cells are left out. Nothing is transformed. At least 3 pairs are needed, and neither X nor Y may be constant. Pairs should be independent, so technical replicates of one sample should be averaged.

**How it's calculated:** With n pairs:

- Pearson: r = Σ(x − x̄)(y − ȳ) / √( Σ(x − x̄)² · Σ(y − ȳ)² ).
- Spearman: rs is Pearson's r of the ranks of X and the ranks of Y, with tied values given the average of the ranks they span ([[Ranks and ties]]).
- The two-tailed [[P value]] tests r = 0 with t = r · √(n − 2) / √(1 − r²) and n − 2 df: P = 2 · P(T > |t|). For Pearson this is exact when X and Y are Gaussian. For Spearman it is the same t approximation, as computed by `scipy.stats.spearmanr`, so it is approximate in small samples and with many ties ([[Exact and approximate P values]]).
- The CI uses Fisher's transformation: z = atanh(r), SE = 1/√(n − 3), CI = tanh(z ± z* · SE) with z* = 1.960 for 95%. For Spearman this is approximate. With n = 3 no CI is given, and if |r| = 1 the CI is r.
- R² = r² (for Spearman, rs²).

**Worked example:** The five points of the linear regression page. Pearson first:

```example
>>> table = XYTable("Response", "Dose", [1, 2, 3, 4, 5], ["Response"], np.array([2, 4, 5, 4, 5], float).reshape(5, 1, 1))
>>> xy_correlation(table, method="pearson")
correlations.Response.r = 0.7746
correlations.Response.r2 = 0.6
correlations.Response.p = 0.1240
correlations.Response.ci_low = -0.3401
correlations.Response.ci_high = 0.9842
```

And Spearman, with a tie in Y:

```example
>>> table = XYTable("Response", "Dose", [1, 2, 3, 4, 5], ["Response"], np.array([2, 4, 5, 4, 5], float).reshape(5, 1, 1))
>>> xy_correlation(table, method="spearman")
correlations.Response.r = 0.7379
correlations.Response.p = 0.1546
correlations.Response.ci_low = -0.4138
correlations.Response.ci_high = 0.9813
```

By hand:

- Pearson: S_xx = 10, S_yy = 6, S_xy = 6, so r = 6 / √60 = 0.7746 and r² = 0.6. t = 0.7746 · √3 / √0.4 = 2.121 with 3 df, P = 2 · `scipy.stats.t.sf(2.121, 3)` = 0.1240 (the same as the P of the slope in the regression).
- Pearson CI: z = atanh(0.7746) = 1.0318, SE = 1/√2 = 0.7071, so z ± 1.960 · 0.7071 = −0.3541 to 2.4177, and tanh gives −0.3401 to 0.9842.
- Spearman: ranks of X are 1, 2, 3, 4, 5. Y = 2, 4, 5, 4, 5 has the two 4s sharing ranks 2 and 3 (2.5 each) and the two 5s sharing ranks 4 and 5 (4.5 each), so the ranks of Y are 1, 2.5, 4.5, 2.5, 4.5. Both rank lists have mean 3. S_xx = 10, S_yy = 4 + 0.25 + 2.25 + 0.25 + 2.25 = 9, S_xy = 4 + 0.5 − 0.5 + 3 = 7, so rs = 7 / √90 = 0.7379.
- Spearman P: t = 0.7379 · √3 / √(1 − 0.5444) = 1.893 with 3 df, P = 2 · `scipy.stats.t.sf(1.893, 3)` = 0.1546. CI: z = atanh(0.7379) = 0.9459, giving tanh(0.9459 ± 1.3859) = −0.4138 to 0.9813.

**Interpreting the results:** **r** (or rs) near +1 means Y rises with X, near −1 means Y falls as X rises, near 0 means no linear (or, for Spearman, no steady) trend. **R squared** is the fraction of the variation in Y that goes with X. The **P value** is the chance of a correlation at least this far from 0 if X and Y were really unrelated; the **CI** shows the range of correlations the data are compatible with. With 5 points the CI here runs from −0.34 to 0.98, so the data say little. Correlation does not show cause; outliers and groups pooled together can create or hide a correlation, so look at the graph. Spearman's CI and P are approximate.

**Options:** Method (Pearson, Spearman); each replicate as a point or only each row's mean; the confidence level, alpha and the P value style. In Advanced, the df of the t test of r can be set by hand (the Fisher CI is unchanged); the sheet lists it under "Changed from the default".

**Checked against:** `scipy.stats.pearsonr` and `spearmanr`.

**References:** Pearson K (1896) *Phil Trans R Soc A* 187:253-318. Spearman C (1904) *Am J Psychol* 15:72-101. Fisher RA (1921) *Metron* 1:3-32.

**Learn more:** [Linear regression and correlation (Handbook of Biological Statistics)](https://www.biostathandbook.com/linearregression.html), [Pearson correlation coefficient (Wikipedia)](https://en.wikipedia.org/wiki/Pearson_correlation_coefficient), [Spearman's rank correlation coefficient (Wikipedia)](https://en.wikipedia.org/wiki/Spearman%27s_rank_correlation_coefficient).

**Related:** [[Linear regression]], [[R squared]], [[Ranks and ties]], [[Confidence interval]], [[P value]], [[Gaussian distribution]]

#### Nonlinear regression (curve fit)

<!-- opens: curvefit, curvefit:log_dose_response, curvefit:dose_response, curvefit:michaelis_menten, curvefit:one_phase, curvefit:exponential_growth, curvefit:gaussian, curvefit:line, curvefit:polynomial_2, curvefit:polynomial_3 -->
<!-- equations: Nonlinear regression -->

**In short:** Fits a curve (a dose-response, an exponential, an enzyme curve, a peak, a polynomial) to each data set by least squares, and reports the best-fit parameters with their standard errors and confidence intervals.

**When to use it:** Y follows a known kind of curve in X and you want its parameters (an EC50, a rate constant, a half-life, Vmax and Km), or you want to compare curves between data sets or between two models. For a straight line the [[Linear regression]] page is simpler and has exact formulas.

**What is done to your data:** Each data set is fitted on its own. By default every replicate is a point (or choose only each row's mean). Rows without X and blank Y cells are left out of the fit; if "Read X off the curve" is ticked, rows with Y but no X are the unknowns. Nothing is transformed or removed, and every point has equal weight, so the fit works on the scale you give it (a growth curve is fitted to Y itself, not to log Y). The dose-response (dose) and Michaelis-Menten models need X ≥ 0. A fit needs more points than free parameters. Any parameter can be fixed at a constant; fixed parameters are not counted in the df.

**How it's calculated:**

- The built-in models (`CURVE_MODELS`):

| Model (key) | Equation | Parameters | Also reported |
|---|---|---|---|
| Dose-response, X is log(dose) (`log_dose_response`) | Y = Bottom + (Top − Bottom) / (1 + 10^((LogEC50 − X) · HillSlope)) | Bottom, Top, LogEC50, HillSlope | EC50 = 10^LogEC50 (CI from LogEC50's), Span |
| Dose-response, X is dose (`dose_response`) | Y = Bottom + (Top − Bottom) / (1 + (EC50 / X)^HillSlope) | Bottom, Top, EC50, HillSlope | Span |
| Michaelis-Menten (`michaelis_menten`) | Y = Vmax · X / (Km + X) | Vmax, Km | |
| One-phase exponential (`one_phase`) | Y = Plateau + (Y0 − Plateau) · exp(−K · X) | Y0, Plateau, K | Half-life = ln 2 / K, Span |
| Exponential growth (`exponential_growth`) | Y = Y0 · exp(K · X) | Y0, K | Doubling time = ln 2 / K |
| Gaussian (`gaussian`) | Y = Amplitude · exp(−½ ((X − Mean) / SD)²) | Amplitude, Mean, SD | Width at half height = 2√(2 ln 2) · SD |
| Straight line (`line`) | Y = Intercept + Slope · X | Intercept, Slope | |
| Quadratic (`polynomial_2`) | Y = B0 + B1·X + B2·X² | B0, B1, B2 | |
| Cubic (`polynomial_3`) | Y = B0 + B1·X + B2·X² + B3·X³ | B0 to B3 | |

- Least squares ([[Least squares]]): the parameters are those that minimise SS = Σ(y − f(x))². The engine uses a trust-region least-squares search. It starts from values worked out from the data (for example Bottom and Top from the smallest and largest means, the EC50 where Y is halfway) and also from six nearby starting points, and keeps the fit with the smallest SS. Polynomials and the line are linear in their parameters and need one start.
- With k free parameters and n points: df = n − k, MS = SS / df, Sy.x = √MS, R² = 1 − SS / SS_total.
- Standard errors come from the Jacobian J, the matrix of the derivatives of the curve with respect to each free parameter at each X (taken numerically at the best fit). The covariance of the parameters is Sy.x² · (JᵀJ)⁻¹ and each SE is the square root of its diagonal element. These are asymptotic: they are exact for a line and approximate for a curve, and can be poor when the data do not cover the curve.
- The CI of a parameter is estimate ± t* · SE with t* from the t distribution with df degrees of freedom, so it is symmetric. The CIs of EC50, half-life and doubling time are transformed from the CIs of LogEC50 and K.
- Ambiguous fits: if the data cannot pin down every parameter (for example only the bottom plateau was measured), one parameter can be almost entirely replaced by the others (its dependency, 1 − 1/(C_ii · (C⁻¹)_ii), is above 0.9999). The sheet then gives no SE or CI, suggests fixing a parameter, leaves the fit out of comparisons and draws its curve dashed. A fit that stopped before converging is treated the same way.
- AICc = n · ln(SS / n) + 2K + 2K(K + 1) / (n − K − 1), where K = k + 1 (the variance counts as one more parameter). It is blank when n − K − 1 ≤ 0.
- Comparing two fits of the same points: when one model is a special case of the other (a parameter fixed, or a line inside a polynomial), the extra sum-of-squares F test is F = ((SS₁ − SS₂) / (df₁ − df₂)) / (SS₂ / df₂), with (df₁ − df₂, df₂) df, where fit 1 is the simpler one. AICc is given for every pair: the difference Δ = AICc(complex) − AICc(simple), and the probability that the simpler model is correct, 1 / (1 + e^(−Δ/2)). If the simpler model somehow has a smaller SS than the model it is a case of, the comparison is marked not reliable.
- Do the data sets differ? One curve fitted to all the data sets together (or with one chosen parameter shared and the others fitted per data set) is compared with a separate fit per data set, by the same F test and AICc. With a straight line and the slope, this is the slopes test of linear regression.
- Unknowns: for each row with Y but no X, the mean Y is read off each curve within the X range of the fit. A Y the curve does not reach there is reported as out of range, not extrapolated.

**Worked example:** The straight-line model, on the five points of the linear regression page, compared with a quadratic. A line is a model with exact formulas, so every number can be checked by hand.

```example
>>> table = XYTable("Response", "Dose", [1, 2, 3, 4, 5], ["Response"], np.array([2, 4, 5, 4, 5], float).reshape(5, 1, 1))
>>> nonlinear_regression(table, model="line", compare="model", compare_model="polynomial_2")
fits.Response.params.Intercept = 2.2
fits.Response.params.Slope = 0.6
fits.Response.se.Intercept = 0.9381
fits.Response.se.Slope = 0.2828
fits.Response.ss_resid = 2.4
fits.Response.df = 3
fits.Response.sy_x = 0.8944
fits.Response.aicc = 26.33
comparisons.Response.complex.ss_resid = 1.257
comparisons.Response.f = 1.818
comparisons.Response.dfn = 1
comparisons.Response.dfd = 2
comparisons.Response.p = 0.3099
```

By hand:

- For a line, J has a column of 1s (for the intercept) and a column of X (for the slope). JᵀJ = [[5, 15], [15, 55]], with determinant 5 · 55 − 15² = 50, so (JᵀJ)⁻¹ = [[55, −15], [−15, 5]] / 50, whose diagonal is 1.1 and 0.1.
- SS_residual = 2.4 with df = 5 − 2 = 3 (the same as in linear regression), so Sy.x² = 0.8. The covariance diagonal is 0.8 · 1.1 = 0.88 and 0.8 · 0.1 = 0.08, so SE(intercept) = √0.88 = 0.9381 and SE(slope) = √0.08 = 0.2828.
- CI of the slope: 0.6 ± 3.182 · 0.2828 = −0.3001 to 1.500 (t* for 3 df is 3.182). These agree exactly with the linear regression sheet.
- AICc of the line: K = 3, so 5 · ln(2.4 / 5) + 6 + 2 · 3 · 4 / (5 − 3 − 1) = −3.671 + 6 + 24 = 26.33.
- The quadratic fit has SS = 1.257 and df = 5 − 3 = 2 (check in R: `deviance(lm(y ~ x + I(x^2)))`). F = ((2.4 − 1.257) / (3 − 2)) / (1.257 / 2) = 1.143 / 0.6286 = 1.818 with (1, 2) df, P = `scipy.stats.f.sf(1.818, 1, 2)` = 0.3099. The extra parameter does not earn its place, so the line is preferred. The quadratic's AICc needs n − K − 1 > 0, which fails with 5 points and 3 parameters, so the AICc difference is blank.

An exponential with exact data shows a nonlinear fit whose parameters are easy to check:

```example
>>> table = XYTable("Growth", "Time", [0, 1, 2, 3], ["Growth"], np.array([2, 4, 8, 16], float).reshape(4, 1, 1))
>>> nonlinear_regression(table, model="exponential_growth")
fits.Growth.params.Y0 = 2.000
fits.Growth.params.K = 0.6931
fits.Growth.derived.Doubling time = 1.000
fits.Growth.ss_resid = 0
```

Here Y doubles each unit of X, so Y = 2 · 2^X = 2 · exp(0.6931 · X): Y0 = 2, K = ln 2 = 0.6931, and the doubling time ln 2 / K = 1. The fit is perfect (SS = 0), so its standard errors are 0. With real, noisy data the fit is not the same as a straight line through log Y, because the engine minimises squared error on the Y scale.

**Interpreting the results:** For each data set the sheet lists the **best-fit values** (with the derived EC50, half-life or doubling time), their **standard errors**, the **confidence intervals** and the **goodness of fit** (df, R², sum of squares, Sy.x, AICc). Check that "Converged" is Yes and "Ambiguous" is No before using the numbers; an ambiguous fit has no SE or CI and should be fixed by constraining a parameter or adding data that cover the curve. A wide CI means the parameter is poorly determined by your data. R² near 1 only says the curve passes near the points; look at the graph. In a model comparison, a small F-test P means the more complex model fits significantly better; AICc favours the model with the lower value, and its probability says how likely the simpler model is to be the correct one. In a data set comparison, a small P means the data sets differ in the parameter (or in the curve as a whole).

**Options:** The model; fixed parameters (for example Bottom = 0 and Top = 100 for data normalized to percent, or HillSlope = 1); each replicate as a point or only each row's mean; reading unknowns off the curve; a comparison (none, with a second fit of another model or the same model with a parameter fixed, does one curve fit every data set, or does one parameter differ between data sets); the confidence level, alpha and the P value style. In Advanced, the residual df can be set by hand (it replaces the fit's df in the CIs); the sheet lists it under "Changed from the default".

**Checked against:** `scipy.optimize.curve_fit` (parameters and standard errors, with and without a fixed parameter); statsmodels `OLS` for the bands of a straight line and its F tests of nested models (a line inside a quadratic; one line for all data sets against a line per data set); the shared-slope test agrees with linear regression's slopes test. Every model recovers its own parameters from exact data with its automatic starting values.

**References:** Motulsky H, Christopoulos A (2004) *Fitting Models to Biological Data using Linear and Nonlinear Regression*. Burnham KP, Anderson DR, *Model Selection and Multimodel Inference* (AICc).

**Learn more:** [Nonlinear regression (Wikipedia)](https://en.wikipedia.org/wiki/Nonlinear_regression), [Non-linear least squares (Wikipedia)](https://en.wikipedia.org/wiki/Non-linear_least_squares), [Akaike information criterion (Wikipedia)](https://en.wikipedia.org/wiki/Akaike_information_criterion).

**Related:** [[Linear regression]], [[Least squares]], [[R squared]], [[Standard error]], [[Confidence interval]], [[F ratio]], [[Residual]], [[Degrees of freedom]]

### Several variables

#### Correlation matrix

<!-- opens: corrmatrix -->
<!-- equations: Correlation matrix -->

**In short:** Gives the correlation of every pair of variables (columns) at once, each with its confidence interval, P value and number of cases.

**When to use it:** You measured several things on the same cases (for example area, intensity and distance of each cell, or several markers per image) and want to see which go together. Each row is one case, each column one variable. For one pair, or to compare a relation between groups, use [[Correlation (Pearson and Spearman)]] or [[Linear regression]] on an XY table.

**What is done to your data:** Each row is a case. With **each pair uses every case with both values** (the default), a pair uses the rows where both of its columns have a value, so n can differ between pairs. With **only cases with every variable**, any row with a blank is left out of every pair. Spearman replaces the values with their ranks (ties get their average rank). Nothing else is changed. A pair with fewer than 3 cases, or a variable that does not vary, is not computed and the other pairs are unaffected.

**How it's calculated:** For each pair of variables x and y, with n cases:

- Pearson r = Σ(x − x̄)(y − ȳ) / √(Σ(x − x̄)² · Σ(y − ȳ)²). Spearman rₛ is the same formula on the ranks.
- t = r · √(n − 2) / √(1 − r²), with n − 2 [[Degrees of freedom|df]]; the two-tailed [[P value]] is 2 · P(T > |t|).
- The [[Confidence interval|confidence interval]] uses Fisher's z: z = atanh(r), SE = 1/√(n − 3), and the limits are tanh(z ± z* · SE), where z* is 1.96 for 95%. For Spearman the interval is approximate.
- With a P value adjustment, the P values of all k(k − 1)/2 pairs are adjusted together as one family (see [[Analyze a stack of P values]]).

**Worked example:** Three measurements on five cells.

```example
Area: 1, 2, 3, 4, 5
Intensity: 2, 4, 5, 4, 5
Distance: 5, 3, 4, 2, 1
>>> correlation_matrix(data)
r[0][1] = 0.7746
p[0][1] = 0.1240
ci_low[0][1] = -0.3401
ci_high[0][1] = 0.9842
r[0][2] = -0.9000
p[0][2] = 0.03739
n[0][1] = 5
```

By hand, for Area and Intensity:

- Means 3 and 4. Deviations of Area −2, −1, 0, 1, 2; of Intensity −2, 0, 1, 0, 1.
- Σ products = 4 + 0 + 0 + 0 + 2 = 6; Σ squares 10 and 6. r = 6 / √60 = 0.7746.
- t = 0.7746 · √3 / √(1 − 0.6) = 2.121 with 3 df, so P = 0.124 (`2 * scipy.stats.t.sf(2.121, 3)`).
- z = atanh(0.7746) = 1.032, SE = 1/√2 = 0.7071, so z ± 1.96 · 0.7071 = −0.354 to 2.418, and tanh gives −0.340 to 0.984.
- Area and Distance: Σ products = −9, Σ squares 10 and 10, so r = −0.9.

**Interpreting the results:** r runs from −1 (a perfect falling line) through 0 (no straight-line relation) to 1 (a perfect rising line). The heat map colours r from blue (−1) to red (1); an asterisk marks pairs whose P is below alpha. With many variables, many pairs are tested and some P values below 0.05 are expected by chance, so consider an adjustment. A correlation shows that two measurements go together, not that one causes the other.

**Options:** Pearson or Spearman; how blank values are handled (each pair, or only complete cases); the P value adjustment (none by default; Holm, Holm-Šidák, Bonferroni, Šidák or an FDR method); the confidence level, alpha and P value style; and the variables to include.

**Checked against:** pandas `DataFrame.corr` (Pearson and Spearman), scipy `pearsonr` and `spearmanr`, and the Holm adjustment written out from its formula.

**References:** Fisher RA (1915) *Biometrika* 10:507–521 (the z transformation).

**Learn more:** [Correlation (Handbook of Biological Statistics)](https://www.biostathandbook.com/linearregression.html), [Pearson correlation coefficient (Wikipedia)](https://en.wikipedia.org/wiki/Pearson_correlation_coefficient).

**Related:** [[Correlation (Pearson and Spearman)]], [[Principal component analysis (PCA)]], [[Adjusted P value]], [[Confidence interval]]

#### Principal component analysis (PCA)

<!-- opens: pca -->
<!-- equations: Correlation matrix -->

**In short:** Combines several variables into a few new ones (components) that hold as much of the variation among the cases as possible, so you can see the main patterns in many measurements at once.

**When to use it:** Several variables measured on the same cases, when you want to see whether the cases form groups, which variables move together, or how many independent patterns there are. PCA describes the data; it does not test a hypothesis and gives no P values.

**What is done to your data:** Only rows with a value in every variable are used (a row with a blank is left out, and the sheet counts them). Each variable is centred (its mean subtracted) and, by default, divided by its SD, so variables in different units count equally. With scaling off, the variables are only centred, so a variable with a large spread dominates.

**How it's calculated:** With n cases and k variables:

- z = (x − x̄) / s for each variable (s with n − 1), or z = x − x̄ without scaling.
- C = zᵀz / (n − 1): the correlation matrix when scaled, the covariance matrix when not.
- The components are the eigenvectors of C, in order of decreasing eigenvalue. Each eigenvalue λ is the variance of the cases along its component. The share of the variance is λ / Σλ (Σλ = k when scaled), and the cumulative share is the running sum.
- Each eigenvector is signed so that its largest coefficient is positive, because a component's sign is arbitrary.
- Loadings = eigenvector × √eigenvalue. With scaling on, a loading is the correlation of a variable with a component.
- Scores = z × eigenvector: each case's position along each component kept.
- Components to keep: **parallel analysis** keeps the components whose eigenvalue is above the 95th percentile of the same eigenvalue in random normal data of the same size (1000 data sets, seed 0, so it is repeatable); **eigenvalue > 1** (Kaiser); a **share of the variance**; or a number set by hand. At least one component is always kept.

**Worked example:** Two variables on four cases.

```example
A: 2, 4, 6, 8
B: 1, 3, 2, 4
>>> pca(data)
eigenvalues[0] = 1.8
eigenvalues[1] = 0.2
share[0] = 0.9
loadings[0][0] = 0.9487
scores.PC1[0] = -1.643
```

By hand:

- A has mean 5 and SD 2.582; B has mean 2.5 and SD 1.291. Scaled: A → −1.162, −0.387, 0.387, 1.162; B → −1.162, 0.387, −0.387, 1.162.
- The correlation is Σ z_A z_B / 3 = (1.35 − 0.15 − 0.15 + 1.35) / 3 = 0.8, so C = [[1, 0.8], [0.8, 1]].
- For a 2 × 2 correlation matrix the eigenvalues are 1 + r = 1.8 and 1 − r = 0.2, with eigenvectors (0.707, 0.707) and (0.707, −0.707). PC1 holds 1.8 / 2 = 90% of the variance.
- Loading of A on PC1 = 0.707 × √1.8 = 0.949.
- The first case's PC1 score = (−1.162 − 1.162) × 0.707 = −1.643. (`scores` is a table with each case's row number and its score on each component kept.)

**Interpreting the results:** The **scree** graph shows each component's eigenvalue; the components above the dashed line (the parallel-analysis threshold, or 1) are kept. The **scores** graph places each case on PC1 and PC2: cases close together have similar profiles. Arrows show each variable's loadings: variables pointing the same way are positively correlated, opposite ways negatively. A component is a mix of variables, so name it from its large loadings. With scaling off, the sheet warns when SDs differ more than tenfold.

**Options:** Scale each variable to unit variance (on by default); the rule for how many components to keep, with its percentage or number; the variables to include. In Advanced, the number of random data sets and the seed of parallel analysis.

**Checked against:** The eigenvalues of numpy's correlation matrix, a singular value decomposition of the standardized data (scores, up to sign), and the loadings as correlations of each variable with each component.

**References:** Jolliffe IT, *Principal Component Analysis*, 2nd ed. (2002). Horn JL (1965) *Psychometrika* 30:179–185 (parallel analysis).

**Learn more:** [Principal component analysis (Wikipedia)](https://en.wikipedia.org/wiki/Principal_component_analysis), [Seeing Theory](https://seeing-theory.brown.edu/), [StatQuest video index](https://statquest.org/video-index/).

**Related:** [[Correlation matrix]], [[Variance]], [[Correlation (Pearson and Spearman)]]

### Contingency tables

#### Contingency tables (Fisher's and chi-square)

<!-- opens: contingency -->
<!-- equations: Contingency tables -->

**In short:** Counts of subjects in a table of rows (groups) by columns (outcomes). It asks whether the proportions of each outcome differ between the rows, and for a 2 × 2 table it also gives the relative risk, the odds ratio and the difference of proportions.

**When to use it:** Your data are counts of whole subjects that fall into categories, such as treated and control animals that died or survived, or cells that did or did not respond. Do not use it for means, percentages or measurements: those belong in a column or grouped table. If the rows are ordered (doses, grades), tick the test for trend. If the rows are test results and the columns are the true condition, read the table as a diagnostic test (see [[Sensitivity and specificity]]).

**What is done to your data:** Every cell must hold a count (a whole number, 0 or more). A blank, negative or fractional cell stops the analysis with a message. Every row and every column needs at least one count. Each count is a number of independent subjects, so N is the total of all cells (see [[N and n]]). Nothing is dropped, merged or transformed. For a 2 × 2 table you choose the group of interest (row 1 by default) and the outcome of interest (column 1 by default); the sheet names both, and choosing the other row gives reciprocal ratios.

**How it's calculated:** Call the cells a, b (first row) and c, d (second row), with the group of interest as the first row and the outcome of interest as the first column.

- **Fisher's exact test** (the default for a 2 × 2 table). With the row and column totals fixed, the chance of seeing a in the top-left cell follows the hypergeometric distribution:

      P(a) = C(a + b, a) · C(c + d, c) / C(N, a + c)

  The two-sided P value is the sum of P over every possible table with the same totals whose probability is no larger than that of the observed table. The engine calls `scipy.stats.fisher_exact`.
- **Pearson's chi-square test** (the default for larger tables, or when chosen). Expected count for each cell = row total × column total / N. Then

      χ² = Σ (observed − expected)² / expected,   df = (rows − 1)(columns − 1)

  and P = P(χ² with df ≥ the observed value), always the upper tail. It is approximate: the sheet warns when more than a fifth of the expected counts are below 5 or any is below 1.
- **Yates' correction** (2 × 2 chi-square only) replaces |observed − expected| by |observed − expected| − 0.5 in each cell before squaring. It is not used with Fisher's test.
- **Relative risk** = (a / (a + b)) / (c / (c + d)). Its CI is Katz's log interval: RR · exp(± z · SE) with SE = √(1/a − 1/(a + b) + 1/c − 1/(c + d)) and z = 1.96 for 95%. When a or c is 0, 0.5 is added to every cell (the sheet says so).
- **Odds ratio** = ad / bc. Its CI is Woolf's logit interval: OR · exp(± z · SE) with SE = √(1/a + 1/b + 1/c + 1/d). When any cell is 0, 0.5 is added to every cell.
- **Difference of proportions** = a/(a + b) − c/(c + d), with Newcombe's hybrid score interval built from the two Wilson intervals. **Number needed to treat** = 1 / |difference|, with the reciprocals of the CI limits of the difference. **Phi** = (ad − bc) / √((a + b)(c + d)(a + c)(b + d)).
- **Chi-square test for trend** (Cochran-Armitage, if ticked, two columns and three or more rows or the reverse): the rows are scored 1, 2, 3…, and the statistic has 1 df. **Cramér's V** = √(χ² / (N (min(rows, columns) − 1))) is given for tables larger than 2 × 2.
- **Diagnostic reading** (if ticked): sensitivity, specificity and the two predictive values with Wilson score intervals, and the two likelihood ratios with log-method intervals (see [[Likelihood ratio]]).

**Worked example:** Ten treated and ten control animals; 7 treated and 2 control animals died.

```example
>>> table = ContingencyTable("Outcome", ["Treated", "Control"], ["Died", "Survived"], [[7, 3], [2, 8]])
>>> contingency(table)
n = 20
fisher_p = 0.06978
p = 0.06978
chi2.chi2 = 5.051
chi2.p = 0.02462
effect.p1 = 0.7
effect.p2 = 0.2
effect.rr = 3.5
effect.rr_ci[0] = 0.9498
effect.rr_ci[1] = 12.90
effect.odds_ratio = 9.333
effect.or_ci[0] = 1.193
effect.or_ci[1] = 72.99
effect.difference = 0.5
effect.phi = 0.5025
```

By hand:

- Row totals 10 and 10, column totals 9 (died) and 11, N = 20. The number of ways to choose the 9 deaths among 20 animals is C(20, 9) = 167,960.
- P(a) = C(10, a) · C(10, 9 − a) / 167,960. The counts C(10, a) · C(10, 9 − a) for a = 0 to 9 are 10, 450, 5400, 25200, 52920, 52920, 25200, 5400, 450, 10. The observed table has a = 7, count 5400.
- Tables no more likely than the observed one: a = 0, 1, 2, 7, 8, 9. Sum = 10 + 450 + 5400 + 5400 + 450 + 10 = 11,720. P = 11,720 / 167,960 = 0.06978. In Python, `scipy.stats.fisher_exact([ [7, 3], [2, 8] ]).pvalue`; in R, `fisher.test(matrix(c(7, 2, 3, 8), 2))$p.value`.
- Chi-square: expected counts are 4.5 (died) and 5.5 (survived) in both rows. χ² = 2 · 2.5²/4.5 + 2 · 2.5²/5.5 = 2.778 + 2.273 = 5.051, df = 1, P = 0.02462 (`scipy.stats.chi2.sf(5.051, 1)`; in R `pchisq(5.051, 1, lower.tail = FALSE)`). The two tests disagree about the 0.05 line here because the table is small; the chi-square P is approximate and the Fisher P is exact.
- With Yates: each |O − E| = 2.5 becomes 2.0, so χ² = 2 · 4/4.5 + 2 · 4/5.5 = 1.778 + 1.455 = 3.232 and P = 0.0722.
- RR = 0.7 / 0.2 = 3.5. SE = √(1/7 − 1/10 + 1/2 − 1/10) = √0.4429 = 0.6655, so the 95% CI is 3.5 · exp(± 1.96 · 0.6655) = 0.9498 to 12.90.
- OR = (7 · 8) / (3 · 2) = 9.333. SE = √(1/7 + 1/3 + 1/2 + 1/8) = √1.1012 = 1.0494, so the CI is 9.333 · exp(± 1.96 · 1.0494) = 1.193 to 72.99.
- Phi = (7 · 8 − 3 · 2) / √(10 · 10 · 9 · 11) = 50 / 99.50 = 0.5025.

**Interpreting the results:** **Test and P value (two-sided)** say whether the proportions of the outcome differ between the rows more than chance would explain. A small P (below alpha, usually 0.05) is evidence of an association; a large one means the data do not show it, not that there is none. The P value does not say how big the difference is. The **Effect size** block does: the percentage with the outcome in each row, the **relative risk** (how many times as likely the outcome is in the first row; 1 means no difference), the **odds ratio** (the same on the odds scale; it approximates the relative risk only when the outcome is rare) and the **difference of proportions**. A confidence interval of a ratio that includes 1, or of a difference that includes 0, goes with a P above alpha. The **number needed to treat** is how many subjects must be in the group of interest to get one more outcome than in the other group; whether that is a benefit or a harm depends on what the outcome is. When the CI of the difference includes 0, the CI of the number needed to treat runs through infinity, and the sheet says so. **Phi** (2 × 2) and **Cramér's V** (larger) go from 0 (no association) to 1. Read the warnings: small expected counts, or fewer than 20 subjects, make the chi-square P and the CIs rough.

**Options:** Test (Fisher's exact test for 2 × 2 and chi-square otherwise, Fisher's only, or chi-square only), Yates' correction (available only with the chi-square test of a 2 × 2 table), ordered groups (adds the test for trend), the group and outcome of interest (2 × 2 only), the diagnostic-test reading (2 × 2 only), alpha, the confidence level (90, 95 or 99%) and the P value style. Options that do not fit the table (Fisher's test on a larger table, for instance) are left out and the sheet says so in its warnings.

**Checked against:** `scipy.stats.fisher_exact`, `chi2_contingency` (with and without Yates' correction) and `contingency.relative_risk` (Katz CI); statsmodels `Table2x2` (odds ratio and its CI), `confint_proportions_2indep` (Newcombe), `proportion_confint` (Wilson) and `Table.test_ordinal_association` (the trend test).

**References:** Fisher RA (1935) *The Design of Experiments*. Katz D et al. (1978) *Biometrics* 34:469–474. Newcombe RG (1998) *Statistics in Medicine* 17:873–890. Agresti A, *Categorical Data Analysis*.

**Learn more:** [Fisher's exact test (Handbook of Biological Statistics)](https://www.biostathandbook.com/fishers.html), [Chi-square test of independence (Handbook of Biological Statistics)](https://www.biostathandbook.com/chiind.html), [Fisher's exact test (Wikipedia)](https://en.wikipedia.org/wiki/Fisher%27s_exact_test).

**Related:** [[Relative risk]], [[Odds ratio]], [[Chi-square]], [[Exact and approximate P values]], [[P value]], [[Confidence interval]], [[Sensitivity and specificity]], [[Likelihood ratio]]

### Diagnostic tests and method comparison

#### ROC curve

<!-- opens: roc -->
<!-- equations: ROC curves -->

**In short:** Shows how well a measurement separates two groups (for example patients and controls) at every possible cutoff, and summarizes it as the area under the curve.

**When to use it:** You have a continuous measurement (a marker level, an intensity, a score) from a group known to have a condition and a group known not to, and you want to know how well the measurement tells them apart and which cutoff to use. If you only want to know whether the two groups' means differ, use a t test. If your data are already counts of test-positive and test-negative subjects, use a [[Contingency tables (Fisher's and chi-square)|contingency table]] with the diagnostic reading.

**What is done to your data:** Each column is one group: controls (without the condition) and patients (with it). Blank or non-numeric cells are left out and counted in a warning. Each remaining value is one subject, so N is the number of subjects in each group (see [[N and n]]). Nothing is transformed. The direction (is a high or a low value positive?) is chosen from the data by default: higher when the patients' mean rank in the pooled data is at least the controls'.

**How it's calculated:**

- The pooled values are ranked (ties get the average rank). The Mann-Whitney statistic U for the patients is the sum of the patients' ranks minus n_p(n_p + 1)/2.
- **Area under the curve** A = U / (n_p · n_c). It is the chance that a randomly chosen patient has a higher value than a randomly chosen control, with ties counting one half. With the direction "lower is positive", A = 1 − U / (n_p · n_c). See [[Area under the curve (AUC)]].
- **Standard error** (Hanley and McNeil 1982). With Q1 = A / (2 − A) and Q2 = 2A² / (1 + A):

      SE² = [A(1 − A) + (n_p − 1)(Q1 − A²) + (n_c − 1)(Q2 − A²)] / (n_p · n_c)

- The CI is A ± z · SE, cut off at 0 and 1 (z = 1.96 for 95%).
- **P value**: a two-tailed z test of A against 0.5, z = (A − 0.5) / SE₀, where SE₀ is the formula above evaluated at A = 0.5. P = 2 · P(Z > |z|). It is approximate (a normal approximation).
- **Cutoffs** are the midpoints between consecutive distinct pooled values, plus one cutoff beyond each end (5% of the pooled range outside the data; when every value is the same, max(5% of |value|, 1)), so the curve runs from (0, 0) to (1, 1).
- At each cutoff, sensitivity = true positives / patients and specificity = true negatives / controls, each with a Wilson score interval. The likelihood ratio is sensitivity / (1 − specificity), undefined where no control is positive (see [[Likelihood ratio]]).
- The **best cutoff** maximizes Youden's J = sensitivity + specificity − 1.

**Worked example:** Four controls and four patients.

```example
Controls: 1, 2, 3, 4
Patients: 3, 5, 6, 7
>>> roc_curve(data["Controls"], data["Patients"])
auc = 0.9062
se = 0.1194
ci_low = 0.6723
ci_high = 1
p = 0.06060
best_cutoff = 4.5
n_controls = 4
n_patients = 4
```

By hand:

- Count the pairs in which the patient's value is higher than the control's. Patient 3: higher than 1 and 2 (2), tied with 3 (0.5). Patients 5, 6 and 7: higher than all four controls (4 each). Total U = 2.5 + 12 = 14.5, so A = 14.5 / 16 = 0.90625.
- Q1 = 0.90625 / 1.09375 = 0.8286 and Q2 = 2 · 0.90625² / 1.90625 = 0.8617. A² = 0.8213. SE² = [0.09375 · 0.90625 + 3 · (0.8286 − 0.8213) + 3 · (0.8617 − 0.8213)] / 16 = (0.0850 + 0.0218 + 0.1212) / 16 = 0.01425, so SE = 0.1194.
- CI: 0.90625 ± 1.96 · 0.1194 = 0.6723 to 1.1402, cut off at 1.
- At A = 0.5: Q1 = Q2 = 1/3, SE₀² = [0.25 + 3 · (1/3 − 0.25) + 3 · (1/3 − 0.25)] / 16 = 0.75 / 16 = 0.046875, SE₀ = 0.2165. z = 0.40625 / 0.2165 = 1.876 and P = 2 · 0.0303 = 0.0606. In Python, `2 * scipy.stats.norm.sf(1.876)`; in R, `2 * pnorm(1.876, lower.tail = FALSE)`.
- Cutoff 4.5 (midway between 4 and 5): patients above are 5, 6, 7, so sensitivity 3/4 = 75%; no control is above 4.5, so specificity is 100%. J = 0.75, the largest of any cutoff.

**Interpreting the results:** The **Area** is 0.5 for a measurement that does not separate the groups at all and 1 for one that separates them perfectly. Its **CI** and the **P value** (is the area different from 0.5?) show how much the area could be due to chance; with few subjects the CI is wide, as here. The **Best cutoff** row gives the cutoff that maximizes sensitivity plus specificity, with both at that cutoff. This is one reasonable choice, not the only one: if a missed case costs more than a false alarm, choose a cutoff with higher sensitivity from the list of all cutoffs, which also gives the intervals and likelihood ratios. The Wilson intervals of sensitivity and specificity are wide when a group is small.

**Options:** Which column holds the controls and which the patients, the direction (Auto, higher values positive, or lower values positive), the confidence level (90, 95 or 99%), alpha and the P value style. With Auto the area is at least 0.5, and the two-sided P value is the same for either direction.

**Checked against:** `scipy.stats.mannwhitneyu` (the area), `sklearn.metrics.roc_auc_score` (where installed), statsmodels `proportion_confint` (Wilson intervals), and the standard error written out by hand from Hanley and McNeil (1982).

**References:** Hanley JA, McNeil BJ (1982) *Radiology* 143:29–36. Wilson EB (1927) *J Am Stat Assoc* 22:209–212.

**Learn more:** [Receiver operating characteristic (Wikipedia)](https://en.wikipedia.org/wiki/Receiver_operating_characteristic), [Sensitivity and specificity (Wikipedia)](https://en.wikipedia.org/wiki/Sensitivity_and_specificity).

**Related:** [[Area under the curve (AUC)]], [[Sensitivity and specificity]], [[Likelihood ratio]], [[Mann-Whitney test]], [[Contingency tables (Fisher's and chi-square)]]

#### Bland-Altman

<!-- opens: blandaltman -->
<!-- equations: ROC curves -->

**In short:** Compares two ways of measuring the same thing. It gives the average disagreement (the bias) and the range that holds about 95% of the differences (the limits of agreement).

**When to use it:** Each item (sample, subject) was measured once by method A and once by method B, and you want to know how far apart the methods are, not whether they correlate. Two methods can correlate perfectly and still disagree by a constant or proportional amount. A paired t test of the same columns asks only whether the average difference is zero; this analysis also shows how wide the spread of differences is.

**What is done to your data:** The two columns are paired by row: row i of one with row i of the other, so each row must be one item measured by both methods. A pair with a blank in either column is left out and counted in a warning. At least 2 complete pairs are needed. For "ratio" every value of both methods must be positive; for "% difference" the mean of a pair cannot be 0. Nothing is logged: ratios are analyzed on their own scale.

**How it's calculated:** Let the measure be d for each pair, one of these (chosen in the dialog):

    difference:     d = A − B
    ratio:          d = A / B
    % difference:   d = 100 (A − B) / ((A + B) / 2)

- **Bias** = mean of d. **SD** = sample SD of d (n − 1 in the denominator).
- **Limits of agreement** = bias ± 1.96 · SD. If the d values are roughly Gaussian, about 95% of differences between the methods lie between them.
- **CI of the bias** = bias ± t* · SD / √n, with t* the Student's t value for n − 1 df.
- **CI of each limit** = limit ± t* · √(3 SD² / n), using the approximate standard error of Bland and Altman (1986). See [[Confidence interval]] and [[Standard deviation]].

**Worked example:** Five items measured by two methods.

```example
A: 10, 12, 14, 16, 18
B: 9, 12, 13, 17, 16
>>> bland_altman(data["A"], data["B"])
bias = 0.6
sd = 1.140
low = -1.635
high = 2.835
bias_ci[0] = -0.8157
bias_ci[1] = 2.016
low_ci[0] = -4.087
low_ci[1] = 0.8173
high_ci[0] = 0.3827
high_ci[1] = 5.287
n = 5
```

By hand:

- Differences A − B: 1, 0, 1, −1, 2. Bias = 3 / 5 = 0.6.
- Deviations from 0.6: 0.4, −0.6, 0.4, −1.6, 1.4. Squares add to 0.16 + 0.36 + 0.16 + 2.56 + 1.96 = 5.2, so variance = 5.2 / 4 = 1.3 and SD = 1.140.
- Limits: 0.6 ± 1.96 · 1.140 = −1.635 and 2.835.
- t* for 4 df and 95% is 2.776 (`scipy.stats.t.ppf(0.975, 4)`; in R `qt(0.975, 4)`). CI of the bias: SE = 1.140 / √5 = 0.5099, so 0.6 ± 2.776 · 0.5099 = −0.8157 to 2.016.
- CI of a limit: SE = √(3 · 1.3 / 5) = √0.78 = 0.8832, so each limit ± 2.776 · 0.8832 = ± 2.452. Lower limit: −1.635 ± 2.452 gives −4.087 to 0.8173. Upper limit: 2.835 ± 2.452 gives 0.3827 to 5.287.

**Interpreting the results:** The **bias** is the average amount by which A reads higher (positive) or lower (negative) than B. If its CI includes 0 (as here), the data do not show a systematic offset. The **limits of agreement** say how far apart the two methods can be for a single item. Whether those limits are acceptable is a scientific judgment: compare them with the largest difference that would not change your conclusions. The CIs of the limits show how well the limits themselves are known; with 5 pairs they are very wide, and the sheet warns below 10 pairs. Look at the Bland-Altman graph too: if the spread of differences grows with the size of the measurement, try the ratio or % difference measure.

**Options:** Method A and method B columns, the measure (difference, ratio or % difference) and the confidence level (90, 95 or 99%). The limits of agreement always use 1.96 SD; the confidence level affects only the CIs.

**Checked against:** The limits and standard errors written out by hand from Bland and Altman (1986) and checked with scipy (Student's t).

**References:** Bland JM, Altman DG (1986) *Lancet* 327:307–310. Bland JM, Altman DG (1999) *Stat Methods Med Res* 8:135–160.

**Learn more:** [Bland-Altman plot (Wikipedia)](https://en.wikipedia.org/wiki/Bland%E2%80%93Altman_plot).

**Related:** [[Paired t test]], [[Correlation (Pearson and Spearman)]], [[Standard deviation]], [[Confidence interval]], [[Paired data]]

### Survival tables

#### Survival curves (Kaplan-Meier)

<!-- opens: survival -->
<!-- equations: Survival analysis -->

**In short:** Estimates the fraction of subjects still event-free over time, even when some subjects were not followed to the end, and tests whether the curves of different groups differ.

**When to use it:** Each subject has a time to an event (death, relapse, tumor reaching a size) or was last seen without the event (censored). Use it for time-to-event data in two or more groups. A plain t test or ANOVA on times would wrongly treat a censored time as an event time (see [[Censored observation]]).

**What is done to your data:** Use an XY table. X holds each subject's time, and each row is one subject. The subject's status goes in the column of its group: 1 if the event happened at that time, 0 if the subject was censored. The other group columns stay blank in that row. A row with a status but no time, or a time with no status, is left out and counted in a warning. A row with a status in more than one group, a negative time, or a status other than 0 or 1 is an error. Each row is one subject, so N is the number of subjects (see [[N and n]]). Nothing is transformed.

**How it's calculated:**

- **Kaplan-Meier (product-limit) curve.** At each distinct time t_i, n_i subjects are still at risk (time ≥ t_i) and d_i have the event:

      S(t) = ∏ (1 − d_i / n_i)   over all t_i ≤ t

  Censored subjects leave the risk set after their time and cause no drop.
- **Greenwood standard error:**

      SE(S(t)) = S(t) · √( Σ d_i / (n_i (n_i − d_i)) )

- **Confidence band** on the log-log scale: S(t)^exp(± z · √G / |ln S(t)|), where G is the Greenwood sum. It stays between 0 and 1 and is undefined where S is 0 or 1.
- **Median survival** is the first event time at which S(t) ≤ 0.5 (undefined if the curve stays above 50%). Its CI is the Brookmeyer-Crowley interval on the log-log scale; an upper limit the data do not reach is shown as ∞.
- **Log-rank (Mantel-Cox) test.** At each event time with n_g at risk and d_g events in group g, and n and d in all groups together, the expected events in group g are E_g = Σ d · n_g / n and the observed are O_g = Σ d_g. The variance of O − E (two groups) is V = Σ d (n − d) / (n − 1) · (n_g / n)(1 − n_g / n). The statistic is χ² = (O₁ − E₁)² / V with df = groups − 1 (the general form uses the matrix of variances and covariances). P = P(χ² ≥ observed), upper tail.
- **Gehan-Breslow-Wilcoxon test** is the same with each event time weighted by the number at risk n, so early differences count more.
- **Log-rank test for trend** (three or more groups, if ticked) scores the groups 1, 2, 3… in table order and tests a linear trend, with 1 df.
- **Hazard ratio** (two groups, first to second), two ways. Mantel-Haenszel: HR = exp((O₁ − E₁) / V), CI = exp(ln HR ± z / √V). Log-rank (O/E): HR = (O₁/E₁) / (O₂/E₂), CI = exp(ln HR ± z √(1/E₁ + 1/E₂)). See [[Hazard ratio]]. Both assume the hazards stay proportional over time.

**Worked example:** Four subjects per group. Group A has events at 3 and 5, a censored subject at 8 and another at 12. Group B has events at 6 and 9, and censored subjects at 10 and 14.

```example
>>> nan = np.nan
>>> time = [3, 5, 8, 12, 6, 9, 10, 14]
>>> table = SurvivalTable.from_columns(time, {"A": [1, 1, 0, 0, nan, nan, nan, nan], "B": [nan, nan, nan, nan, 1, 1, 0, 0]})
>>> survival(table)
subjects = 8
curves.A.survival[0] = 0.75
curves.A.se[0] = 0.2165
curves.A.survival[1] = 0.5
curves.A.se[1] = 0.25
medians.A[0] = 5
medians.B[0] = 9
logrank.observed[0] = 2
logrank.expected[0] = 1.512
logrank.chi2 = 0.2634
logrank.df = 1
logrank.p = 0.6078
hazard_ratio.mantel_haenszel[0] = 1.715
hazard_ratio.logrank[0] = 1.646
```

By hand, group A (the table of the product-limit method):

    time   at risk   events   censored   S(t)               Greenwood sum
    3      4         1        0          1 − 1/4 = 0.75     1/(4·3) = 0.0833
    5      3         1        0          0.75 · 2/3 = 0.50  0.0833 + 1/(3·2) = 0.25
    8      2         0        1          0.50               0.25
    12     1         0        1          0.50               0.25

- SE at time 3 = 0.75 · √0.0833 = 0.2165. SE at time 5 = 0.50 · √0.25 = 0.25.
- Median of A: the first event time at which S ≤ 0.5 is 5. For B the curve is 0.75 after time 6 and 0.75 · 2/3 = 0.5 after time 9, so the median is 9.
- Log-rank, group A at each event time (n_A, n_B at risk; n = n_A + n_B; one event each time):

      time   n_A   n_B   E_A = n_A/n   V = (n_A/n)(n_B/n)
      3      4     4     0.5000        0.2500
      5      3     4     0.4286        0.2449
      6      2     4     0.3333        0.2222
      9      1     3     0.2500        0.1875

  With one event per time, d (n − d) / (n − 1) = 1. O_A = 2 (events at 3 and 5), E_A = 1.5119, V = 0.9046.
- χ² = (2 − 1.5119)² / 0.9046 = 0.2634, df = 1, P = 0.6078. In Python, `scipy.stats.chi2.sf(0.2634, 1)`; in R, `pchisq(0.2634, 1, lower.tail = FALSE)`.
- Hazard ratio (A to B), Mantel-Haenszel: exp((2 − 1.5119) / 0.9046) = exp(0.5396) = 1.715. Log-rank (O/E): E_B = 4 − 1.5119 = 2.4881 (the expected events add to the 4 observed), so HR = (2 / 1.5119) / (2 / 2.4881) = 1.646.

**Interpreting the results:** The **Subjects** rows count the events and censored subjects per group. **Median survival** is the time by which half the group has had the event; "Undefined" means the curve never fell to 50%, and a CI ending in ∞ means the data do not rule out a longer median. The **log-rank P value** tests the null hypothesis that the groups share one survival curve; a small P (below alpha) is evidence that they differ, a large one that the data do not show a difference (here, with 8 subjects, there is little power). The **Gehan-Breslow-Wilcoxon** P weights early times more, so it can differ when curves cross or diverge early or late; choose the test before looking. The **hazard ratio** says how much faster (above 1) or slower (below 1) the first group has the event than the second at any moment; a CI that includes 1 goes with P above alpha. The sheet also gives the ratio of the two medians. Look at the curves: the tests and the hazard ratio assume curves that do not cross.

**Options:** Whether the groups are in order (adds the log-rank test for trend; available with three or more groups), the confidence level (90, 95 or 99%), alpha and the P value style.

**Checked against:** statsmodels `SurvfuncRight` (the curve, Greenwood standard errors, median and its CI) and `survdiff` (the log-rank and Gehan-Breslow chi-square), with hand-counted small examples.

**References:** Kaplan EL, Meier P (1958) *J Am Stat Assoc* 53:457–481. Mantel N (1966) *Cancer Chemother Rep* 50:163–170. Brookmeyer R, Crowley J (1982) *Biometrics* 38:29–41.

**Learn more:** [Kaplan-Meier estimator (Wikipedia)](https://en.wikipedia.org/wiki/Kaplan%E2%80%93Meier_estimator), [Logrank test (Wikipedia)](https://en.wikipedia.org/wiki/Logrank_test).

**Related:** [[Hazard ratio]], [[Censored observation]], [[Chi-square]], [[Confidence interval]], [[Exact and approximate P values]]

### Data processing

#### Transform, normalize and remove baseline

<!-- opens: process -->

<!-- equations: Data processing -->

**In short:** Makes a new data table from a column table by transforming each value (for example to its log), scaling each column to 0 to 100%, expressing each value as a fraction of a total, subtracting or dividing by a baseline column, or combining two columns row by row.

**When to use it:** Before an analysis, when the raw values do not suit the test. Take logs when values grow by factors (intensities, areas, fold changes), so a t test compares geometric means. Normalize to bring experiments with different scales to a common 0 to 100% scale. Remove a baseline to express treated values as a percent of control, or, row by row, to remove the variation between replicates (what a paired design is for).

**What is done to your data:** The source table is never changed. The new table follows its source: edit the source and the new table, its results and graphs update. Editing the new table by hand keeps its numbers and stops it following. A value with no result (the log of 0 or of a negative number, a division by 0) becomes a blank cell, and the table's notes count how many. Existing blanks stay blank. Only the ticked columns are changed in Transform, Normalize and Fraction of total; the others are copied. Choose Y as a value and K as a constant you type.

**How it's calculated:** Y is a value, K a constant, and all calculations are done column by column.

- **Transform:** each Y becomes Y × K, Y ÷ K, Y + K, Y − K, Y^K, Y², √Y, 1/Y, log10(Y), log2(Y), ln(Y), 10^Y, e^Y, |Y|, logit = ln(Y / (1 − Y)), arcsine(√Y) or the z-score (Y − mean) / SD, with the mean and SD (n − 1) of its column. Logs need Y > 0; √Y needs Y ≥ 0; logit needs 0 < Y < 1; arcsine needs 0 ≤ Y ≤ 1. For percentages, divide by 100 first.
- **Normalize:** Y' = 100 × (Y − Z) / (H − Z) for each ticked column, where Z (0%) is the column's smallest value, its first value, or a number, and H (100%) is its largest value, its last value, its sum, or a number. With "fractions" the 100 is dropped. If Z and H are equal, the column cannot be normalized and is left blank (the notes say so).
- **Fraction of total:** Y / T × 100 (or without the 100), where T is the column total, the grand total of the ticked columns, or the total of the ticked values in the same row.
- **Remove baseline:** each column is compared with a baseline column B, using either the mean of B (one number, for unpaired groups) or the value of B in the same row (paired). The four operations: Y − B, Y / B, 100 × Y / B (% of baseline) and 100 × (Y − B) / B (% change). The baseline column can be kept or left out.
- **Column math:** one new column from columns A and B, row by row: A − B, A / B, A + B, A × B, 100 × (A − B) / B, or log2(A / B).

**Worked example:** Normalize one column to percent, then express treated values as a percent of the control mean.

```example
Dose: 2, 4, 6, 10
>>> out = normalize(table)[0]
>>> from types import SimpleNamespace
>>> SimpleNamespace(values=dict(first=out["Dose"].values[0], second=out["Dose"].values[1], third=out["Dose"].values[2], fourth=out["Dose"].values[3]))
first = 0
second = 25
third = 50
fourth = 100
```

```example
Control: 10, 20, 30
Treated: 30, 40, 50
>>> out = remove_baseline(table, "Control", "percent", "mean")[0]
>>> from types import SimpleNamespace
>>> SimpleNamespace(values=dict(control_1=out["Control"].values[0], control_3=out["Control"].values[2], treated_1=out["Treated"].values[0], treated_3=out["Treated"].values[2]))
control_1 = 50
control_3 = 150
treated_1 = 150
treated_3 = 250
```

By hand:

- Normalize: Z = smallest = 2 and H = largest = 10, so H − Z = 8. Y' = 100 × (Y − 2) / 8 gives 100 × 0/8 = 0, 100 × 2/8 = 25, 100 × 4/8 = 50 and 100 × 8/8 = 100.
- Remove baseline: the mean of Control is (10 + 20 + 30) / 3 = 20. Each value becomes 100 × Y / 20, so Control gives 50, 100, 150 and Treated gives 150, 200, 250. The control averages 100%, as it should.
- Dividing by a control mean changes the units but not the P value of a comparison between two treated groups. Dividing by each replicate's own control (baseline "same row") removes the variation between replicates instead.

**Interpreting the results:** There is no results sheet: the new table is the result, and the notes under it say exactly what was done (for example "Normalized: 0% = smallest value in each column, 100% = largest value in each column") and how many values were left blank. Check those notes. Then analyze the new table like any other: a t test on log10 values compares geometric means, and a confidence interval of a difference in logs is a ratio after you take the antilog (see [[Geometric mean]]). Normalizing every column to its own smallest and largest values uses the data twice, so the 0% and 100% points are themselves uncertain; for a comparison between groups, prefer a fixed control or a number you know.

**Options:** Transform: the function, K, and the ticked columns. Normalize: what 0% and 100% are (with a number box when you choose "A number"), percentages or fractions, and the ticked columns. Fraction of total: the total used, percentages or fractions, and the ticked columns. Remove baseline: the baseline column, the operation, whether to use the baseline's mean or its value in the same row, and whether to keep the baseline column. Column math: columns A and B and the operation. **Change processing…** above the new table changes any of these later.

**Checked against:** The tests for data processing check each function against hand-calculated values and numpy.

**References:** Bland JM, Altman DG (1996) Transforming data. *BMJ* 312:770.

**Learn more:** [Data transformations (Handbook of Biological Statistics)](https://www.biostathandbook.com/transformation.html), [Normalization (Wikipedia)](https://en.wikipedia.org/wiki/Normalization_(statistics)).

**Related:** [[Geometric mean]], [[Lognormal distribution]], [[Paired data]], [[Unpaired t test on logs]], [[Ratio paired t test]]

## Terms

### Adjusted P value

<!-- aliases: Adjusted P, Adjusted P value, Adjusted P values, q value, q values, Q (desired FDR), Q, Discoveries, Estimated number of true null hypotheses (m0), Estimated number of true null hypotheses, Number of P values -->

**In short:** A P value that has been made larger to allow for the other comparisons you ran, so that it can be compared with alpha directly.

**In detail:** If you compare ten pairs of conditions, there is about a 40% chance that at least one of them will look "significant" at P < 0.05 by chance alone, even if nothing differs. An adjusted P value corrects for that. It answers: how likely is a result this extreme anywhere in the whole family of comparisons if nothing differs? You then compare it with alpha as usual. A false discovery rate method gives a **q value** instead: the lowest Q (accepted share of false discoveries) at which that comparison would still count as a discovery. Adjusted P values are never smaller than the unadjusted P, except that a q value from the two-stage method of Benjamini, Krieger and Yekutieli can be smaller.

**How it's calculated:** With m comparisons in the family and unadjusted P values P:

- Bonferroni: P_adj = min(1, m · P).
- Šidák: P_adj = 1 − (1 − P)^m.
- Holm-Šidák (step-down): sort P ascending; P_adj(r) = the largest, over s ≤ r, of 1 − (1 − P(s))^(m − s + 1).
- Tukey and Dunnett: P_adj is the chance, under no differences, that the studentized range (Tukey) or the largest absolute t (Dunnett) is at least as large as observed.
- Dunn: P_adj = P × the number of comparisons, capped at 1 (shown as >0.9999, or >.999 or >0.999 in the APA and NEJM styles).
- Benjamini-Hochberg q: q(r) = the smallest, over s ≥ r, of N · P(s) / s. Benjamini-Yekutieli multiplies this by H = 1 + 1/2 + … + 1/N. The two-stage method of Benjamini, Krieger and Yekutieli first estimates how many null hypotheses are true.

**Where you see it:** In the multiple comparisons rows of a results sheet ("Adjusted P" in each comparison row such as "A vs. B"), in **Analyze a stack of P values** (rows "Discoveries", "Significant", "Q (desired FDR)" and "Estimated number of true null hypotheses (m0)"), and behind the stars of each comparison.

**Learn more:** [Multiple comparisons problem (Wikipedia)](https://en.wikipedia.org/wiki/Multiple_comparisons_problem)

**Related:** [[Multiple comparisons]], [[Family-wise error rate]], [[Alpha]], [[P value]], [[False discovery rate (q values)]], [[Analyze a stack of P values]]

### Alpha

<!-- aliases: alpha, Significant, Significant?, Significantly different (P < 0.05)?, Significantly different ?, Significant diff. among means (P < 0.05)?, Significant diff. among means ?, Statistically significant (P < 0.05)?, Statistically significant ?, Are SDs significantly different (P < 0.05)?, Are SDs significantly different ?, Are the medians signif. different (P < 0.05)?, Are the medians signif. different ?, Do the medians vary signif. (P < 0.05)?, Do the medians vary signif. ?, Is linear trend significant (P < 0.05)?, Is linear trend significant ?, Is nonlinear variation significant (P < 0.05)?, Is nonlinear variation significant ?, Is the correlation different from zero?: Significant?, Is the slope different from zero?: Significant?, Significance level -->

**In short:** The cut-off for P that you choose before the experiment; a result is called significant when P is below it, and 0.05 is the usual choice.

**In detail:** Alpha is the false-positive rate you are willing to accept. At alpha = 0.05, if there is truly no difference you will still call a result significant in about 1 experiment in 20. It is a decision rule, not a measure of how large or important an effect is. Choose alpha before you look at the data, and keep it when the result is close: P = 0.051 and P = 0.049 are not different kinds of evidence. The "Yes" or "No" in a significance row only compares P with alpha. The stars use fixed cut-offs (see [[P value summary (stars)]]) and do not follow your alpha.

**How it's calculated:** A result is significant when P < alpha. The default is alpha = 0.05. The confidence level of intervals is set separately in the **Confidence interval** box of each dialog (95% by default); it does not follow alpha.

**Where you see it:** The **Alpha** box of each analysis dialog. On the results sheet, the rows that end in "(P < 0.05)?" (for example "Significantly different (P < 0.05)?", "Significant diff. among means (P < 0.05)?") show the alpha you used in place of 0.05; rows named "Significant?" do the same. Several messages ("cannot reach alpha") compare the smallest possible P with it.

**Related:** [[P value]], [[Family-wise error rate]], [[Multiple comparisons]], [[Adjusted P value]], [[P value summary (stars)]]

### Area under the curve (AUC)

<!-- aliases: AUC, Area, Area under the curve, Area under the ROC curve, Area under the ROC curve (AUC) -->

**In short:** For a ROC curve, the chance that a randomly chosen positive sample scores higher than a randomly chosen negative one: 0.5 is no better than a coin flip and 1 is perfect separation.

**In detail:** Suppose a marker is measured in control cells and in treated cells that responded. The ROC curve plots sensitivity against 1 − specificity for every possible cut-off. The area under it summarises how well the marker separates the two groups without choosing a cut-off. With "Auto" direction, the engine orients the curve so the area is at least 0.5.

**How it's calculated:** AUC = U / (n₁ · n₂), where U is the Mann-Whitney statistic of the patients against the controls and a tie counts one half. The standard error is Hanley and McNeil's: with A the area, Q1 = A / (2 − A) and Q2 = 2A² / (1 + A),

    SE² = [A(1 − A) + (n_patients − 1)(Q1 − A²) + (n_controls − 1)(Q2 − A²)] / (n_patients · n_controls)

The confidence interval is A ± z · SE, limited to 0 to 1. The P value is a two-tailed z test of A against 0.5: z = (A − 0.5) / SE₀, with SE₀ the standard error computed at A = 0.5.

**Where you see it:** The **ROC curve** sheet, section "Area under the ROC curve": rows "Area", "Std. Error", "95% CI", "P value" and "Significant?".

**Learn more:** [Receiver operating characteristic (Wikipedia)](https://en.wikipedia.org/wiki/Receiver_operating_characteristic)

**Related:** [[ROC curve]], [[Sensitivity and specificity]], [[Likelihood ratio]], [[Ranks and ties]]

### Biological replicate

<!-- aliases: Biological replicates, Number of replicates (total), Number of replicates, Replicate, Replicates, Independent replicate, N (replicates) -->

**In short:** An independent sample: one animal, one culture, one independently prepared experiment. Its count is N.

**In detail:** Two wells seeded from the same flask on the same day are not biological replicates of each other; two cultures prepared and treated on different days are. A statistical test about a condition asks whether independent samples differ, so each dot that goes into a test should be a biological replicate. When you have many images or cells from one sample, they describe that one replicate. Average them (or pool them as described in [[N and n]]) to get its single value. Using images or cells as if they were replicates is [[Pseudoreplication]].

**How it's calculated:** There is no formula. In a table with a Replicate column (a SuperPlot table), the analyses use one value per replicate and column: the replicate's mean, or its median if you chose the **Replicate mean** menu setting. For percents from CellQuant results, a replicate's value is Σ counts ÷ Σ denominators × 100, not the mean of the images' percents.

**Where you see it:** The **Replicates** column above a column table, the note over the grid giving N and n per column, the **Nested** sheet (rows "Number of replicates (total)" and the per-group "N = … replicates; n = … values"), and the **Methods** text.

**Learn more:** [Pseudoreplication (Wikipedia)](https://en.wikipedia.org/wiki/Pseudoreplication)

**Related:** [[N and n]], [[Technical replicate]], [[Pseudoreplication]], [[SuperPlot]], [[Nested t test and nested one-way ANOVA]]

### Censored observation

<!-- aliases: Censored, Censoring, Censored subject, Censored subjects, Censored observations -->

**In short:** A subject that did not have the event during follow-up, so all you know is that its time to the event is longer than the time it was watched.

**In detail:** In a survival experiment on mice, an animal still alive when the study ends, or one that leaves the study for an unrelated reason, is censored. Its follow-up time is real information (it survived at least that long) but not the time of an event. Dropping censored animals, or treating their last day as a death, biases the curve. Kaplan-Meier analysis uses them correctly.

**How it's calculated:** In the table, the status of a subject is 1 for an event and 0 for censored. A censored subject stays in the risk set up to its time and leaves it afterwards, so it counts in n_i at every earlier event time but is never counted as an event (d_i). The curve draws it as a tick.

**Where you see it:** The **Survival** sheet, section "Subjects" ("4 subjects: 3 events, 1 censored"), and the ticks on the Kaplan-Meier graph.

**Learn more:** [Censoring (Wikipedia)](https://en.wikipedia.org/wiki/Censoring_(statistics))

**Related:** [[Survival curves (Kaplan-Meier)]], [[Hazard ratio]]

### Changed from the default

<!-- aliases: Changed from the default, Set by hand, Overrides, Advanced overrides, Advanced -->

**In short:** A section that lists every setting you changed by hand under Advanced, with the value the engine would otherwise have computed.

**In detail:** Most analyses work out every number from your data. Under **Advanced** you can replace some of them, for example the degrees of freedom of a t test, or the number of comparisons counted by a Bonferroni-type correction. Such a change can make P smaller or larger, so it must be reported. The engine records it in three places so that it is not lost: the section "Changed from the default", a warning, and the end of the methods sentence.

**How it's calculated:** Nothing is calculated. Each line gives the label, your value and the computed default, for example "df: 6 (default 8)". An Advanced setting is used only while Advanced is ticked.

**Where you see it:** The section "Changed from the default" of a results sheet, the warning "Set by hand: df = 6 (computed 8). Report this with the result." and the **Methods** text ("Changed from the default: df = 6 (computed 8).").

**Related:** [[Methods sentence]], [[Degrees of freedom]], [[Unpaired t test]], [[Two-way ANOVA]]

### Chi-square

<!-- aliases: Chi square, Chi-squared, chi-square test, Bartlett's statistic (corrected), χ², χ2 -->

**In short:** A test statistic that adds up how far observed counts are from the counts expected under the null hypothesis, scaled by the expected counts.

**In detail:** Chi-square (χ²) appears in several places in this engine. In a contingency table it compares observed counts (for example cells that did or did not respond in each condition) with the counts expected if rows and columns were unrelated. The same distribution is used to turn the statistic into a P value in the Kruskal-Wallis and Friedman tests (when P is not exact), Bartlett's test, the normality test of D'Agostino and Pearson, and the log-rank test. The distribution has a single parameter, its degrees of freedom. The P value is the area above the observed value, so a large χ² gives a small P.

**How it's calculated:** For a contingency table, χ² = Σ (observed − expected)² / expected, summed over all cells, with expected = row total × column total / grand total and df = (rows − 1)(columns − 1). Yates' correction, if chosen for a 2 × 2 table, reduces each |observed − expected| by 0.5 first. P = P(χ²_df ≥ observed). The test is approximate: the sheet warns when more than a fifth of the expected counts are below 5 or any is below 1.

**Where you see it:** The **Contingency** sheet (row "Test" names it, then "P value (two-sided)"), and rows "Chi-square, df" in the **Survival** sheet (log-rank and Gehan-Breslow-Wilcoxon tests), "Chi-square, df" in the mixed-effects "Was the matching effective?" section, "Kruskal-Wallis statistic" and "Friedman statistic" when P comes from χ².

**Learn more:** [Chi-squared test (Wikipedia)](https://en.wikipedia.org/wiki/Chi-squared_test)

**Related:** [[Contingency tables (Fisher's and chi-square)]], [[Degrees of freedom]], [[Exact and approximate P values]], [[Kruskal-Wallis test]]

### Coefficient of variation

<!-- aliases: CV, Coefficient of variation, % CV, Relative standard deviation -->

**In short:** The standard deviation as a percentage of the mean, so that the spread of measurements on different scales can be compared.

**In detail:** A cell area with mean 500 and SD 50 varies by 10%, the same relative variability as a signal of mean 5 and SD 0.5. The CV is only meaningful for ratio-scale data that cannot be negative and have a true zero (counts, intensities, areas). It is misleading for values that can be near or below zero, where the mean is close to 0.

**How it's calculated:** CV = 100 · s / x̄ (percent), with s the sample SD (n − 1 in the denominator). It needs n ≥ 2.

**Where you see it:** **Column statistics**, row "Coefficient of variation".

**Learn more:** [Coefficient of variation (Wikipedia)](https://en.wikipedia.org/wiki/Coefficient_of_variation)

**Related:** [[Column statistics]], [[Standard deviation]], [[Mean]]

### Confidence interval

<!-- aliases: CI, 95% CI, 95% confidence interval, Confidence interval, Confidence intervals, Lower 95% CI of mean, Upper 95% CI of mean, Lower 95% CI of geo. mean, Upper 95% CI of geo. mean, 95% CI of ratio, 95% CI of the geometric mean ratio, 95% CI of the mean log difference, 95% CI: Slope, 95% CI: Y-intercept, Correlation: 95% CI, Correlation: 95% CI (approximate), Lower limit 95% CI, Upper limit 95% CI, 95% CI of difference, CI of the mean, CI of difference, 94.44% CI of difference, 93.75% CI, 94.53% CI, 95% CI (asymptotic): Slope, 95% CI (asymptotic): Intercept, 95% CI (asymptotic): Km, 95% CI (asymptotic): Vmax -->

**In short:** A range, computed from your data, that would contain the true value in 95% of experiments like this one.

**In detail:** A mean of 11 with a 95% interval of 3.7 to 18.3 says the data are compatible with true means anywhere in that range. The width tells you how precisely the quantity is known: it shrinks as the number of biological replicates grows. For a difference, an interval that excludes 0 goes with P < 0.05 (for the same test and the same level). An interval is more informative than a bare P value because it shows the size of the effect. With N = 3 the multiplier is 4.30 SEM on each side, so intervals from three replicates are wide.

**How it's calculated:** For a mean or a difference, estimate ± t* · SE, where t* is the (1 − (1 − level)/2) quantile of the t distribution with the test's df. For the mean of a column, SE = s / √n and df = n − 1; for a difference, SE and df are those of the test. The geometric mean's interval is exp(mean of ln x ± t* · SEM of ln x). Other intervals have their own method: Fisher's z for correlation, Woolf's logit for an odds ratio, Wilson score intervals for proportions, and order statistics for the Hodges-Lehmann difference (the achievable level nearest to the one asked for, printed with the level actually reached, for example "94.44% CI of difference"). Intervals are always two-sided.

**Where you see it:** Rows such as "95% confidence interval" (t tests), "Lower 95% CI of mean" and "Upper 95% CI of mean" (Column statistics), "95% CI: Slope" (Linear regression), "95% CI" (ROC curve, Bland-Altman) and the "95% CI" inside each multiple comparison row. The level comes from the **Confidence interval** box of the dialog.

**Learn more:** [Confidence limits (Handbook of Biological Statistics)](https://www.biostathandbook.com/confidence.html)

**Related:** [[Standard error]], [[SEM]], [[Degrees of freedom]], [[Effect size]], [[P value]]

### Degrees of freedom

<!-- aliases: df, DF, DFn, DFd, Dfd, Degrees of freedom, Goodness of fit: Degrees of freedom, Is the slope different from zero?: DFn, Number of degrees of freedom -->

**In short:** The number of independent pieces of information left after estimating what you had to estimate; it sets the exact shape of the t, F and chi-square distributions.

**In detail:** Five values give five pieces of information. Once you have used them to calculate their mean, only four are free to vary (the fifth is fixed by the mean). The SD therefore has n − 1 df. In tests, df measures how much information the error estimate rests on: more df means a narrower t distribution and a smaller critical value. Welch's test and the nested t test give df that are not whole numbers, from the Welch-Satterthwaite equation. Counting technical replicates as if they were independent inflates df and makes P falsely small.

**How it's calculated:** Examples used by this engine:

- One sample: n − 1.
- Unpaired t test: n_A + n_B − 2. Paired t test: n − 1.
- Welch's t test: SE⁴ / ((s_A²/n_A)² / (n_A − 1) + (s_B²/n_B)² / (n_B − 1)).
- Ordinary one-way ANOVA: k − 1 (between groups) and N − k (residual).
- Repeated-measures ANOVA: k − 1 and (n − 1)(k − 1), both multiplied by the Geisser-Greenhouse epsilon when that correction is used.
- Linear regression: n − 2 (n − 1 through the origin); nonlinear regression: n − k parameters.
- Chi-square test of a contingency table: (rows − 1)(columns − 1).

**Where you see it:** Rows "t, df", "F, DFn, DFd", "F (DFn, DFd)", "W (DFn, DFd)", "Chi-square, df", "Goodness of fit: Degrees of freedom", and the "DF" column of an ANOVA table. Under Advanced you can set the df by hand; the sheet then lists it under "Changed from the default".

**Learn more:** [Degrees of freedom (Wikipedia)](https://en.wikipedia.org/wiki/Degrees_of_freedom_(statistics))

**Related:** [[t ratio]], [[F ratio]], [[Chi-square]], [[Sum of squares]], [[Mean square]], [[Geisser-Greenhouse epsilon]], [[Changed from the default]]

### Effect size

<!-- aliases: Effect size, Effect sizes, Cohen's d, Cohen's d_z, Hedges' g, Hedges' g_z, Glass's delta (SD of A), Glass's delta, Cramér's V, Phi, Cohen's d (paired) -->

**In short:** The size of a difference or relationship, in units that do not depend on the sample size, so it can be compared across experiments.

**In detail:** P tells you whether an effect is detectable; the effect size tells you how big it is. A tiny difference becomes "highly significant" if you measure enough cells, so always read the effect size with the P value. This engine reports several, each a difference divided by a spread. Cohen's d is the difference in group means in units of the pooled SD. As a rough guide, 0.2 is small, 0.5 medium and 0.8 large, but what matters biologically depends on the system. Hedges' g is d with a small-sample correction.

**How it's calculated:** With B − A the difference of means, s_p the pooled SD, and ν = n_A + n_B − 2:

- Cohen's d = (x̄_B − x̄_A) / s_p.
- Hedges' g = J · d, with J = Γ(ν/2) / (√(ν/2) · Γ((ν − 1)/2)), about 1 − 3 / (4(n_A + n_B) − 9).
- Glass's Δ = (x̄_B − x̄_A) / s_A, scaled by the SD of the first column (the control).
- Paired: d_z = mean(d) / SD(d), and g_z = J(n − 1) · d_z, with d the paired differences.
- For a contingency table: phi = (ad − bc) / √(row and column totals multiplied); Cramér's V = √(χ² / (N · (min(rows, columns) − 1))) for larger tables.
- R squared and eta squared are effect sizes too; see [[R squared]].

**Where you see it:** Section "Effect size" of the t test sheets (rows "Cohen's d", "Hedges' g", "Glass's delta (SD of A)"; "Cohen's d_z" and "Hedges' g_z" for paired), and rows "Phi" and "Cramér's V" on the **Contingency** sheet.

**Learn more:** [Effect size (Wikipedia)](https://en.wikipedia.org/wiki/Effect_size)

**Related:** [[R squared]], [[Confidence interval]], [[Unpaired t test]], [[Paired t test]], [[Standard deviation]]

### Exact and approximate P values

<!-- aliases: Exact or approximate P value?, Exact or approximate P value, Exact or estimate?, Exact or estimate, Exact P value, Approximate P value, Approximate P, Approximate P (first round), Exact, Approximate -->

**In short:** An exact P comes from listing every possible outcome of the test; an approximate P uses a smooth curve (usually Gaussian or chi-square) that is close when samples are large.

**In detail:** With only a few values per group, rank-based tests have a limited number of possible outcomes, so the exact P can be counted. For instance, two groups of three can arrange themselves in only 20 ways. The smooth approximations are poor for such small samples, so the engine counts exactly whenever the rule below allows. For large samples counting is impractical and the approximation is accurate.

**How it's calculated:** The cut-offs this engine uses:

- Mann-Whitney: exact, ties included, when the smaller column has at most 100 values and n_A · n_B ≤ 20 000; otherwise the Gaussian approximation with the tie-corrected variance (no continuity correction unless requested).
- Wilcoxon matched pairs: exact, ties included, for fewer than 200 nonzero differences; otherwise Gaussian.
- Kruskal-Wallis: exact when the number of splits of the midranks N! / (Πnᵢ! · Πm_s!) is at most 2 000 000; otherwise chi-square with k − 1 df.
- Friedman: exact unless (k!)ⁿ > 10⁹; otherwise chi-square with k − 1 df.
- Two-sample Kolmogorov-Smirnov: exact for n_A · n_B < 10 000 (conservative with ties).
- Normality tests, the F test, t tests and ANOVA use known distributions, so they are not described as exact or approximate in the results.
- Contingency tables: Fisher's test is exact; the chi-square P is approximate.

**Where you see it:** Rows "Exact or approximate P value?" (Mann-Whitney, Kruskal-Wallis, Friedman), "Exact or estimate?" (Wilcoxon) and warnings that give the smallest P an exact test could reach.

**Related:** [[P value]], [[Mann-Whitney test]], [[Wilcoxon matched-pairs test]], [[Kruskal-Wallis test]], [[Friedman test]], [[Ranks and ties]], [[Chi-square]]

### F ratio

<!-- aliases: F, F ratio, F test, F statistic, F*, Treatment: F, Is the slope different from zero?: F, F test to compare variances -->

**In short:** The ratio of two mean squares: the variation your factor explains divided by the variation left unexplained. A large F suggests a real effect.

**In detail:** In one-way ANOVA, F compares how far apart the group means are with how much the values vary within the groups. If the groups really have the same mean, F is expected to be near 1, so a large F gives a small P. F has two degrees of freedom: for the numerator (DFn) and for the denominator (DFd). The same ratio also compares two variances (the F test in the unpaired t test sheet).

**How it's calculated:**

- Ordinary one-way ANOVA: F = MS_between / MS_within, with (k − 1, N − k) df.
- Repeated measures: F = MS_treatment / MS_residual; the matching test is F = MS_subjects / MS_residual.
- Two-way ANOVA: F = MS_effect / MS_residual for each effect.
- Welch's W and Brown-Forsythe F* have their own formulas (see [[Welch's and Brown-Forsythe ANOVA]]).
- F test of equal variances: F = larger s² / smaller s², with (n − 1) df for each column.
- Linear regression: F = SS_regression / (SS_residual / (n − 2)), df (1, n − 2).
- P = P(F_(DFn, DFd) > F), the upper tail.

**Where you see it:** Rows "F", "F (DFn, DFd)", "F* (DFn, DFd)", "F, DFn, DFd", the "F" part of every ANOVA table row, "Is the slope different from zero?: F" and "Treatment: F (DFn, DFd)" on the mixed-effects sheet.

**Learn more:** [F-test (Wikipedia)](https://en.wikipedia.org/wiki/F-test)

**Related:** [[Mean square]], [[Sum of squares]], [[Degrees of freedom]], [[Ordinary one-way ANOVA]], [[Two-way ANOVA]], [[t ratio]]

### Family-wise error rate

<!-- aliases: Family-wise error rate, FWER, Family-wise alpha, Alpha (family-wise), Family, Families, Number of families, Number of comparisons per family -->

**In short:** The chance of at least one false positive among a whole set (family) of comparisons.

**In detail:** If every comparison is tested at alpha = 0.05 on its own, the chance of at least one false positive among 10 independent true-null comparisons is about 40%. A family-wise method keeps that chance at alpha for the whole set. This is stricter than controlling each comparison, and the cost is lower power. The family is the set of comparisons you consider together; with two reference groups, each reference can be its own family or all comparisons can form one family (stricter).

**How it's calculated:** With m comparisons each at level α_c and independent tests, FWER = 1 − (1 − α_c)^m. Šidák solves this for α_c = 1 − (1 − α)^(1/m); Bonferroni uses α/m. Tukey and Dunnett use the exact joint distribution of the statistics. A comparison is significant when its [[Adjusted P value]] is below alpha.

**Where you see it:** Multiple comparisons headers: "Number of families", "Number of comparisons per family", "Alpha (family-wise)", and the dialog's **Families** choice (one per reference, or all in one).

**Learn more:** [Family-wise error rate (Wikipedia)](https://en.wikipedia.org/wiki/Family-wise_error_rate)

**Related:** [[Multiple comparisons]], [[Adjusted P value]], [[Alpha]], [[Multiple comparisons after ANOVA]], [[False discovery rate (q values)]]

### Fixed and random effects

<!-- aliases: Fixed effect, Fixed effects, Random effect, Random effects, Random effects model, Fixed and random effects, Mixed model -->

**In short:** A fixed effect is a factor whose levels you chose and care about (control vs. treated); a random effect is a factor whose levels are a sample from a larger population (the particular mice or experiments you happened to use).

**In detail:** The treatment is the fixed effect: you want to know what these treatments do. The replicates are random effects: you do not care about experiment 3 itself, only about how much experiments vary, because that variation is what the treatment effect must be judged against. Treating replicates as random and giving each a variance is what makes the nested and mixed-effects analyses correct for pseudoreplication.

**How it's calculated:** In the mixed-effects model: value = group mean + subject effect + error, with the subject effect and the error Gaussian (a random intercept for each row), fitted by REML. The fixed effect of treatment is tested with a Wald F test; the random effects are reported as variances and SDs. In the nested analysis the replicate effect ρ is random and nested within the group, fitted by the method of moments.

**Where you see it:** The **Mixed-effects** sheet (row "Treatment: F (DFn, DFd)" for the fixed effect; rows "Subject: SD", "Subject: variance", "Residual: SD", "Residual: variance" for the random effects), and the **Nested** sheet's "Variance components" section.

**Learn more:** [Random effects model (Wikipedia)](https://en.wikipedia.org/wiki/Random_effects_model)

**Related:** [[Mixed-effects model]], [[Nested t test and nested one-way ANOVA]], [[Variance components]], [[Intraclass correlation]], [[Residual]]

### Gaussian distribution

<!-- aliases: Gaussian, Normal distribution, Normal, Normality, Gaussian distribution, Passed normality test (alpha=0.05)?, Passed normality test ?, Skewness, Kurtosis, Bell curve, K2, A2, KS distance -->

**In short:** The symmetrical bell-shaped distribution (also called normal) that many tests assume the values, or their errors, follow.

**In detail:** Many measurements, such as the mean of many cells in a replicate, are roughly Gaussian because many small independent effects add up. Counts, intensities and ratios are often right-skewed and are better described by a [[Lognormal distribution]]. The t tests and ANOVA assume Gaussian values within each group (or Gaussian residuals); they are fairly robust with similar group sizes, but not with N = 3 and a strongly skewed measure. A normality test cannot prove normality: small samples nearly always pass, and large samples fail for deviations too small to matter. Look at the data and use the biology to decide.

**How it's calculated:** A Gaussian is set by its mean and SD. About 68% of values lie within 1 SD of the mean, 95% within 1.96 SD. The engine's normality tests are D'Agostino-Pearson (n ≥ 8), Shapiro-Wilk (n ≥ 3), Anderson-Darling (n ≥ 8) and Kolmogorov-Smirnov with Lilliefors' P (n ≥ 5). Skewness (0 for a symmetric distribution) and excess kurtosis (0 for a Gaussian) are the sample-adjusted G₁ and G₂.

**Where you see it:** The **Normality tests** sheet (rows "K2", "W", "A2", "KS distance", "Passed normality test (alpha=0.05)?"), and "Skewness" and "Kurtosis" on **Column statistics**.

**Learn more:** [Normal distribution (Wikipedia)](https://en.wikipedia.org/wiki/Normal_distribution)

**Related:** [[Normality and lognormality tests]], [[Lognormal distribution]], [[Unpaired t test]], [[Ranks and ties]]

### Geisser-Greenhouse epsilon

<!-- aliases: Geisser-Greenhouse's epsilon, Geisser-Greenhouse, Greenhouse-Geisser, Epsilon, ε, Sphericity correction -->

**In short:** A number between 1/(k − 1) and 1 that measures how far repeated-measures data depart from sphericity; the test's degrees of freedom are multiplied by it.

**In detail:** Epsilon = 1 means the data meet the sphericity assumption and nothing changes. Smaller epsilon means the differences between conditions vary unequally, which makes the ordinary F test too optimistic. Multiplying both df by epsilon gives a larger, more honest P. This engine always reports epsilon, and uses the correction by default (the dialog box "Don't assume sphericity (Geisser-Greenhouse)").

**How it's calculated:** With S the k × k covariance matrix of the columns, C = I − J/k and S* = CSC (the double-centred matrix),

    ε = (tr S*)² / ((k − 1) · tr(S*²))

The corrected P uses F with df (ε(k − 1), ε(n − 1)(k − 1)). For the mixed-effects model with blanks, S is built from pairwise-complete rows (this engine's choice); if a pair of columns has fewer than 2 complete rows, ε is undefined and P is left uncorrected with a warning.

**Where you see it:** Row "Geisser-Greenhouse's epsilon" on the **Repeated-measures** and **Mixed-effects** sheets, and "Assume sphericity (equal variability of differences)?" which reads No when the correction is used.

**Related:** [[Sphericity]], [[Repeated-measures one-way ANOVA]], [[Mixed-effects model]], [[Degrees of freedom]]

### Geometric mean

<!-- aliases: Geometric mean, Geometric mean of A, Geometric mean of B, Geometric mean of ratios (B/A), Geometric mean of ratios, Ratio of geometric means (B/A), Ratio of geometric means, Geometric SD factor, Difference of log10 means (B - A), Mean of differences -->

**In short:** The typical value of data that act by multiplication: the antilog of the mean of the logs.

**In detail:** Fold changes, ratios, titres and many intensities are multiplicative: a doubling and a halving cancel out. The arithmetic mean of 0.5 and 2 is 1.25, but their geometric mean is 1, which matches the biology. It is also the natural summary if the data follow a lognormal distribution. All values must be greater than zero.

**How it's calculated:** Geometric mean = exp(mean of ln x), the same for any log base. The geometric SD factor is exp(SD of ln x): multiply and divide the geometric mean by it to get the ±1 SD range. Its confidence interval is exp(mean of ln x ± t* · SEM of ln x). In the lognormal t tests the engine compares means of log₁₀ values and reports the ratio of the geometric means (B/A) with its interval; in the ratio paired t test it gives the geometric mean of the per-pair ratios, 10^mean(log₁₀ ratio).

**Where you see it:** **Column statistics** (rows "Geometric mean", "Geometric SD factor", "Lower 95% CI of geo. mean", "Upper 95% CI of geo. mean"), and the **Unpaired t test on logs** and **Ratio paired t test** sheets.

**Learn more:** [Geometric mean (Wikipedia)](https://en.wikipedia.org/wiki/Geometric_mean)

**Related:** [[Mean]], [[Lognormal distribution]], [[Unpaired t test on logs]], [[Ratio paired t test]], [[Column statistics]]

### Hazard ratio

<!-- aliases: Hazard ratio, Hazard ratio (A / B), HR, Mantel-Haenszel, Log-rank (O/E), Log-rank, Ratio of median survival -->

**In short:** How many times higher the instantaneous risk of the event is in one group than in the other, at any moment.

**In detail:** A hazard ratio of 2 means that, at any time, the animals in the first group are dying (or relapsing) at twice the rate of the second group, among those still at risk. A hazard ratio of 1 means no difference, and below 1 means the first group does better. It assumes the hazards stay proportional over time: look at the curves, which should not cross. The sheet also gives the ratio of the two median survivals as a more intuitive number.

**How it's calculated:** Two groups, first to second, with O and E the observed and expected events from the log-rank calculation and V the variance of O₁ − E₁:

- Mantel-Haenszel: HR = exp((O₁ − E₁) / V), with CI exp(ln HR ± z / √V).
- Log-rank (O/E): HR = (O₁/E₁) / (O₂/E₂), with CI exp(ln HR ± z · √(1/E₁ + 1/E₂)). It is undefined when a group has no events; the Mantel-Haenszel ratio is still given, but with very few events its interval is very wide.

**Where you see it:** The **Survival** sheet, section "Hazard ratio (A / B)": rows "Mantel-Haenszel" and "Log-rank (O/E)", and section "Ratio of median survival".

**Learn more:** [Hazard ratio (Wikipedia)](https://en.wikipedia.org/wiki/Hazard_ratio)

**Related:** [[Survival curves (Kaplan-Meier)]], [[Censored observation]], [[Odds ratio]], [[Relative risk]]

### Hodges-Lehmann estimate

<!-- aliases: Hodges-Lehmann, Hodges-Lehmann estimate, Hodges-Lehmann pseudo-median, Difference: Hodges-Lehmann, Pseudo-median, Pseudomedian -->

**In short:** A rank-based estimate of the typical difference between two groups that is not thrown off by outliers.

**In detail:** The ordinary difference of means is pulled by extreme values. The Hodges-Lehmann estimate is the median of all possible pairwise differences, so it measures the shift between the groups in the same rank-based way as the test. It goes with the Mann-Whitney and Wilcoxon tests: the confidence interval and the P value agree with each other. It is not the same as the difference of medians, which is also shown.

**How it's calculated:**

- Mann-Whitney: the median of all n_A · n_B differences B_j − A_i. The confidence interval uses order statistics [d(k), d(N + 1 − k)] of those differences. Only some levels can be reached, so the engine uses the achievable level nearest to the one you asked for (which can be below it) and prints the level actually reached, for example "94.44% CI of difference".
- Wilcoxon matched pairs: the pseudo-median is the median of the Walsh averages (d_i + d_j) / 2 for i ≤ j, with its interval at the nearest achievable level.

**Where you see it:** The **Mann-Whitney** sheet (rows "Difference: Actual", "Difference: Hodges-Lehmann", "94.44% CI of difference"), and the **Wilcoxon** sheet and one-sample Wilcoxon in Column statistics (row "Hodges-Lehmann pseudo-median").

**Learn more:** [Hodges-Lehmann estimator (Wikipedia)](https://en.wikipedia.org/wiki/Hodges%E2%80%93Lehmann_estimator)

**Related:** [[Mann-Whitney test]], [[Wilcoxon matched-pairs test]], [[Median]], [[Ranks and ties]], [[Confidence interval]]

### Interaction

<!-- aliases: Interaction, Interactions, Interaction effect -->

**In short:** In two-way ANOVA, an effect of one factor that depends on the level of the other.

**In detail:** Suppose you treat cells with a drug and measure two cell lines. If the drug lowers the signal by the same amount in both lines, the effects are additive and there is no interaction. If it lowers the signal strongly in one line and not at all in the other, the two factors interact. When the interaction is large, a main effect (the average drug effect across lines) can be misleading, because it averages over effects that differ.

**How it's calculated:** The model is y = μ + α_i + β_j + (αβ)_ij + ε, with the interaction term (αβ)_ij. Its sum of squares is the rise in residual SS when the interaction alone is left out (Type III). With equal n in every cell, SS_interaction = n · Σ(ȳ_ij − ȳ_i· − ȳ_·j + ȳ)², with (a − 1)(b − 1) df. F = MS_interaction / MS_residual. The interaction cannot be fitted when a cell is empty or when every cell has one value; the additive model is then used.

**Where you see it:** The **Two-way ANOVA** sheet, row "Interaction" in "Source of variation" (percent of total variation), "Significance" and the ANOVA table. The dialog checkbox is "Fit the interaction (row × data set)".

**Learn more:** [Interaction (statistics) (Wikipedia)](https://en.wikipedia.org/wiki/Interaction_(statistics))

**Related:** [[Two-way ANOVA]], [[Main effect]], [[F ratio]], [[Sum of squares]]

### Intraclass correlation

<!-- aliases: Intraclass correlation, ICC, Share of the variation among replicates (intraclass correlation), Share of the variation among replicates, Intraclass correlation coefficient -->

**In short:** The share of the total variation that lies among replicates rather than within them: near 1 means replicates differ a lot and cells within a replicate agree.

**In detail:** If you image 100 cells from each of three experiments and the cells within an experiment are almost identical but the experiments differ, the ICC is high. Then the 100 cells carry little more information than one value per experiment, so the effective sample size is close to the number of replicates. A low ICC means replicates behave alike and the cells do add information. In the nested analysis, a large ICC is the reason pseudoreplication is so misleading.

**How it's calculated:** ICC = σ̂²_rep / (σ̂²_rep + σ̂²_resid), where σ̂²_rep = max(0, (MS_rep − MS_resid) / c_r) is the variance among replicates (set to 0 when negative, and a note says so), σ̂²_resid = MS_resid is the variance within replicates, and c_r is a coefficient set by the numbers of values per replicate (it equals m when every replicate has m values).

**Where you see it:** The **Nested** sheet, section "Variance components", row "Share of the variation among replicates (intraclass correlation)".

**Learn more:** [Intraclass correlation (Wikipedia)](https://en.wikipedia.org/wiki/Intraclass_correlation)

**Related:** [[Variance components]], [[Pseudoreplication]], [[Nested t test and nested one-way ANOVA]], [[Technical replicate]]

### Least squares

<!-- aliases: Least squares, Least-squares, Ordinary least squares, Best-fit values, Best-fit values: Slope, Best-fit values: Y-intercept, Best-fit values: X-intercept, Best-fit values: 1/slope, Best-fit values: Intercept, Best-fit values: Km, Best-fit values: Vmax, Goodness of fit: Sum of squares, Goodness of fit: AICc, Data: Converged, Data: Ambiguous, Equation -->

**In short:** Choosing the line or curve that makes the sum of the squared vertical distances from the points as small as possible.

**In detail:** Every fit in QuantAnalysis uses least squares: linear regression, nonlinear regression and the ANOVA models. Squaring means a point twice as far from the line counts four times as much, so one wild point pulls hard on the fit. For a straight line there is a closed-form answer. For curves the engine searches: it starts from values taken from the data and from six nearby starts, and keeps the fit with the smallest sum of squares. Any parameter can be fixed to a constant.

**How it's calculated:** The residual sum of squares is SS = Σ(y − ŷ)². For a line y = a·x + b: a = Σ(x − x̄)(y − ȳ) / Σ(x − x̄)², b = ȳ − a·x̄. For a curve with k parameters and n points, df = n − k and Sy.x = √(SS / df). Standard errors come from the asymptotic covariance Sy.x² (JᵀJ)⁻¹, with J the derivatives of the curve with respect to the parameters, so they can be poor when the data do not cover the curve. If the data cannot pin every parameter down, the fit is flagged ambiguous and no SE or CI is given.

**Where you see it:** The **Linear regression** and **Nonlinear regression** sheets: rows "Best-fit values: …", "Std. Error: …", "Goodness of fit: Sum of squares", "Goodness of fit: Sy.x", "Data: Converged" and "Data: Ambiguous".

**Learn more:** [Least squares (Wikipedia)](https://en.wikipedia.org/wiki/Least_squares)

**Related:** [[Linear regression]], [[Nonlinear regression (curve fit)]], [[Residual]], [[R squared]], [[Sum of squares]]

### Likelihood ratio

<!-- aliases: Likelihood ratio, Likelihood ratios, Likelihood ratio (positive), Likelihood ratio (negative), LR+, LR- -->

**In short:** How many times more likely a positive (or negative) test result is in subjects with the condition than in those without it.

**In detail:** A positive likelihood ratio of 10 means a positive result is ten times more likely in a case than in a non-case. Unlike predictive values, likelihood ratios do not depend on how common the condition is in your table. Values above 1 for a positive result, and below 1 for a negative result, support the condition.

**How it's calculated:** LR+ = sensitivity / (1 − specificity), and LR− = (1 − sensitivity) / specificity. The confidence interval uses the log method of Simel, Samsa and Matchar: for LR+, exp(ln LR+ ± z · √(1/a − 1/(a + c) + 1/b − 1/(b + d))), with a, b, c, d the cells of the table (rows test positive and negative, columns condition present and absent); LR− uses the same with c and d. A ratio whose denominator is 0 is reported as undefined.

**Where you see it:** The **Contingency** sheet when **Diagnostic test** is ticked (rows "Likelihood ratio (positive)" and "Likelihood ratio (negative)"), and the cutoff table of the **ROC curve** sheet.

**Learn more:** [Likelihood ratios in diagnostic testing (Wikipedia)](https://en.wikipedia.org/wiki/Likelihood_ratios_in_diagnostic_testing)

**Related:** [[Sensitivity and specificity]], [[Contingency tables (Fisher's and chi-square)]], [[ROC curve]]

### Lognormal distribution

<!-- aliases: Lognormal, Log-normal, Lognormality, Lognormal distribution, Passed lognormality test (alpha=0.05)?, Passed lognormality test ? -->

**In short:** A right-skewed distribution whose logarithm is Gaussian.

**In detail:** Intensities, cell areas, titres, expression levels and concentrations often follow a lognormal distribution: values bunch near the low end with a long tail of high values, and effects act by multiplication. The arithmetic mean and SD describe such data badly. Take the logs, then use ordinary methods on the logs, which is what the unpaired t test on logs and the ratio paired t test do. Also use the [[Geometric mean]] as the summary.

**How it's calculated:** The lognormality tests are the same tests as the normality tests, run on ln(x). Every value must be greater than 0. The base of the logarithm does not change the results.

**Where you see it:** The **Normality and lognormality tests** sheet, with "Passed lognormality test (alpha=0.05)?" in place of "Passed normality test…", and the **Unpaired t test on logs** and **Ratio paired t test** sheets.

**Learn more:** [Log-normal distribution (Wikipedia)](https://en.wikipedia.org/wiki/Log-normal_distribution)

**Related:** [[Gaussian distribution]], [[Geometric mean]], [[Normality and lognormality tests]], [[Unpaired t test on logs]], [[Transform, normalize and remove baseline]]

### Main effect

<!-- aliases: Main effect, Main effects, Row factor, Column factor, Rows (row factor), Data sets (column factor), Row effect, Column effect, Equal numbers of values in every cell -->

**In short:** In two-way ANOVA, the effect of one factor averaged over the levels of the other.

**In detail:** In a table with drug (rows) and cell line (data sets), the row main effect asks whether the drug changes the signal on average across cell lines, and the column main effect asks whether the lines differ on average across drugs. Both are valid when there is no interaction. When the interaction is large, read the simple effects (the comparison inside each row or each data set) instead.

**How it's calculated:** Sums of squares are Type III: each effect's SS is the rise in residual SS when that effect alone is left out of the full model. With equal n in every cell, SS_row = b · n · Σ(ȳ_i·· − ȳ)² and SS_column = a · n · Σ(ȳ·j· − ȳ)², with a − 1 and b − 1 df. F = MS_effect / MS_residual. Comparisons of a main effect use least-squares means, the average of a level's cell means with every cell weighted equally.

**Where you see it:** The **Two-way ANOVA** sheet: rows "Row factor" and "Column factor" in "Source of variation", "Significance" and the ANOVA table.

**Related:** [[Two-way ANOVA]], [[Interaction]], [[F ratio]], [[Sum of squares]], [[Multiple comparisons after ANOVA]]

### Mean

<!-- aliases: Mean, Arithmetic mean, Average, Mean of A, Mean of B, Mean difference (B - A), Actual mean, Theoretical mean, Discrepancy, Harmonic mean, Quadratic mean, Sum -->

**In short:** The sum of the values divided by how many there are.

**In detail:** The mean is the balance point of the data and the summary that goes with the t test and ANOVA. It is pulled by outliers and by long tails, so for skewed data consider the [[Median]] or, for multiplicative data, the [[Geometric mean]]. In a SuperPlot analysis, the mean is taken over biological replicates (each replicate's own value is its mean or median over its cells or images), not over the cells.

**How it's calculated:** x̄ = Σx / n. Blank cells are left out. Column statistics also give the harmonic mean, n / Σ(1/x) (all x > 0), and the quadratic mean, √(Σx² / n). The difference between two columns is always B − A.

**Where you see it:** **Column statistics** (row "Mean"), the t test sheets (rows "Mean of A" and "Mean of B", "Difference between means (B - A) ± SEM"), the ANOVA group summary lines ("mean = …"), and the one-sample test rows "Actual mean", "Theoretical mean" and "Discrepancy" when you give a hypothetical value.

**Learn more:** [Statistics of central tendency (Handbook of Biological Statistics)](https://www.biostathandbook.com/central.html)

**Related:** [[Median]], [[Geometric mean]], [[Standard deviation]], [[SEM]], [[Column statistics]]

### Mean square

<!-- aliases: MS, Mean square, Mean squares -->

**In short:** A sum of squares divided by its degrees of freedom: an estimate of a variance.

**In detail:** In ANOVA, each source of variation has a mean square. If the null hypothesis is true, the mean square between groups and the mean square within groups both estimate the same variance, so their ratio F is near 1. The residual mean square (MS_residual) is the pooled variance of the values around their group means; its square root is the pooled SD used by the multiple comparisons that follow an ordinary ANOVA.

**How it's calculated:** MS = SS / df. For ordinary one-way ANOVA: MS_between = SS_between / (k − 1) and MS_within = SS_within / (N − k).

**Where you see it:** The "MS" part of each line of an ANOVA table ("Treatment (between columns)", "Residual (within columns)"), and in nested and two-way ANOVA tables. Multiple comparison standard errors use MS_residual.

**Related:** [[Sum of squares]], [[F ratio]], [[Degrees of freedom]], [[Variance]], [[Ordinary one-way ANOVA]]

### Median

<!-- aliases: Median, Median of A, Median of B, Median of differences (B - A), Median of differences, Actual median, Theoretical median, Discrepancy (median - hypothetical), 50% Percentile, Difference: Actual -->

**In short:** The middle value when the data are sorted: half the values are below it and half above.

**In detail:** The median is not affected by a few extreme values, so it is a better summary of skewed data than the mean. With an even number of values it is the average of the two middle ones. It is the summary that goes with rank-based tests such as Mann-Whitney, Kruskal-Wallis and Wilcoxon, although those tests compare ranks, not medians directly.

**How it's calculated:** The 50th percentile, by the same rule as the other percentiles (see [[Percentiles and quartiles]]).

**Where you see it:** **Column statistics** (row "Median"), "Median of A" and "Median of B" on the Mann-Whitney and Kolmogorov-Smirnov sheets, "Median of differences (B - A)" on the Wilcoxon sheet, and the median survival on the Survival sheet.

**Related:** [[Mean]], [[Percentiles and quartiles]], [[Hodges-Lehmann estimate]], [[Mann-Whitney test]]

### Methods sentence

<!-- aliases: Methods, Methods sentence, Methods text, Methods paragraph -->

**In short:** A ready-made sentence describing the analysis that was run, to copy into the methods section of a paper.

**In detail:** Every results sheet has a **Methods** box under it. It states the test, the tails of P, the alpha, the multiple comparisons method and its family-wise alpha, and anything changed by hand under Advanced. For replicate tables it also states N and n. You still have to add what one dot is, and the software and its version. Copying it makes sure the report matches what was actually computed.

**How it's calculated:** It is built from the options used. For example: "Unpaired t test (pooled variance) comparing B with A (two-tailed P, alpha = 0.05). Changed from the default: df = 6 (computed 8)."

**Where you see it:** The **Methods** box under a results sheet, in the exported results ("Methods:" line), and in `result.methods` when using the engine from code.

**Related:** [[Changed from the default]], [[N and n]], [[Alpha]]

### Multiple comparisons

<!-- aliases: Multiple comparisons, Multiple comparison, Multiple comparisons test, Multiple comparisons procedure, Number of comparisons, Reference group(s), Reference group, Groups tested against, Post-hoc tests, Post hoc tests -->

**In short:** Testing more than two groups, or many pairs, increases the chance of a false positive unless the P values are adjusted.

**In detail:** The more comparisons you make, the more likely one of them reaches P < 0.05 by chance. A one-way ANOVA tells you that the means are not all equal; the multiple comparison tests that follow it say which pairs differ, and adjust their P values for the number of comparisons. Decide what you want to compare before the experiment: all pairs (Tukey), each group against a control (Dunnett), or a few chosen pairs (Šidák or Holm-Šidák). Choosing the comparisons after seeing the data makes the adjustment wrong. The more comparisons you run, the less power each has.

**How it's calculated:** Each comparison "A vs. B" reports A − B (against a reference, reference − group), its standard error, df, test statistic, unadjusted P, adjusted P and confidence interval. See [[Multiple comparisons after ANOVA]] for the methods and when to use each one, and [[Adjusted P value]] for the formulas.

**Where you see it:** The section "Multiple comparisons: …" of an ANOVA results sheet, with rows "Number of families", "Number of comparisons per family", "Alpha (family-wise)", "Reference group(s)" and one row for each comparison.

**Learn more:** [Multiple comparisons (Handbook of Biological Statistics)](https://www.biostathandbook.com/multiplecomparisons.html)

**Related:** [[Multiple comparisons after ANOVA]], [[Family-wise error rate]], [[Adjusted P value]], [[Dunnett's test]], [[Dunn's test]], [[False discovery rate (q values)]]

### N and n

<!-- aliases: N, N and n, Number of values, Number of values (total), Number of missing values, Sample size, Number of groups, Number of groups (columns), Number of treatments (columns), Number of data sets (columns), Number of rows, Data: Number of XY pairs, Data: Points analyzed, Data: Number of X values, Controls (controls) n, Controls n, Patients (patients) n, Patients n, Data sets analyzed -->

**In short:** N is the number of biological replicates (independent samples); n is the number of technical replicates (images, fields or cells) within them.

**In detail:** A claim about a condition is a claim about biological replicates. 600 cells from one culture per group is still N = 1 per group, however large n is. Cells or images of one sample share its biology and its handling, so they are not independent. Testing them as if they were is [[Pseudoreplication]]: it makes n large and P falsely small. QuantAnalysis therefore tests one value per replicate and column, shows both N and n on the table, graph axes and results, and puts a red warning on a table that has one dot per object. Many sheets name their counts simply "n"; there it is the number of values the test used, so check what a value represents.

**How it's calculated:** When a table has replicates, each replicate counts once, using its mean or its median over its n values. A percent pooled over a replicate's images is

    replicate percent = Σ counts ÷ Σ denominators × 100

so an image with more cells weighs more within its replicate (this is not the mean of the images' percents). A per-object value is averaged over every counted object of the replicate. A SuperPlot shows both levels: each cell or image as a small dot coloured by its replicate, each replicate as a large outlined marker, and the mean ± SD of the replicates.

**Where you see it:** The note above a column table with replicates, the axis labels, "Number of values", "Sample size, A", "Number of pairs", the **Nested** sheet's data summary ("N = … replicates; n = … values"), and the **Methods** text. In the **Import CellQuant results** dialog, **Dot =** sets what one value is (biological replicate N by default, or an image or object, which is a technical replicate n).

**Learn more:** [Pseudoreplication (Wikipedia)](https://en.wikipedia.org/wiki/Pseudoreplication)

**Related:** [[Biological replicate]], [[Technical replicate]], [[Pseudoreplication]], [[SuperPlot]], [[Nested t test and nested one-way ANOVA]]

### Odds ratio

<!-- aliases: Odds ratio, OR -->

**In short:** The odds of an outcome in one group divided by the odds in the other: 1 means no association.

**In detail:** If 8 of 10 treated cells die and 3 of 10 control cells die, the odds of dying are 8/2 = 4 in the treated cells and 3/7 = 0.43 in the controls, so the odds ratio is about 9.3. Odds ratios are always valid for case-control designs. For common outcomes they overstate the relative risk, so read them as odds, not as "times more likely". The sheet names the group of interest and the outcome of interest in the label; choosing the other row or column gives reciprocals.

**How it's calculated:** With a, b the group of interest with and without the outcome, and c, d the other group: OR = ad / bc. Confidence interval (Woolf's logit method): exp(ln OR ± z · √(1/a + 1/b + 1/c + 1/d)). If any cell is 0, 0.5 is added to every cell (the sheet says so).

**Where you see it:** The **Contingency** sheet, row "Odds ratio (Treated vs Control, Died)" for a 2 × 2 table (the group and outcome names come from your table).

**Learn more:** [Odds ratio (Wikipedia)](https://en.wikipedia.org/wiki/Odds_ratio)

**Related:** [[Relative risk]], [[Contingency tables (Fisher's and chi-square)]], [[Hazard ratio]], [[Confidence interval]]

### One- and two-tailed P values

<!-- aliases: One- or two-tailed P value?, One- or two-tailed P value, One-tailed, Two-tailed, One tailed, Two tailed, One-sided, Two-sided, P value (one tailed), P value (two-sided), P value (two tailed), One-tailed P value, Two-tailed P value -->

**In short:** A two-tailed P counts a difference in either direction as extreme; a one-tailed P counts only the direction you predicted before the experiment.

**In detail:** Use the two-tailed P unless you could not have interpreted a result in the other direction at all, and you decided this before seeing the data. When the data go the predicted way, the one-tailed P is half the two-tailed P, so choosing the tail after looking doubles the false-positive rate. When the data go the opposite way, the one-tailed P is above 0.5, and the test reports no evidence for the prediction. Confidence intervals are always two-sided.

**How it's calculated:** For a t test, the two-tailed P = 2 · P(T > |t|). For the alternative B > A (or B < A), P = P(T > t) (or P(T < t)); this equals half the two-tailed P when t has the predicted sign, and 1 − (two-tailed P)/2 when it does not. In the engine, `alternative="greater"` means B > A and `"less"` means B < A. Fisher's exact test is two-sided: the sum of the probabilities of every table with the same margins that is no more likely than the one observed.

**Where you see it:** The **P value** box of the two-column dialogs (choose two-tailed or one-tailed), the row "One- or two-tailed P value?" on the sheet, and rows "P value (one tailed)" (the check of pairing in paired tests) and "P value (two-sided)" (contingency tables).

**Related:** [[P value]], [[Alpha]], [[Unpaired t test]], [[Contingency tables (Fisher's and chi-square)]]

### Outlier

<!-- aliases: Outlier, Outliers, Number of outliers, Total number of outliers, Robust centre, Robust SD of residuals (RSDR), Robust SD of residuals, Critical G, Grubbs' G (first round), Outlier detection -->

**In short:** A value that lies so far from the rest that it is unlikely to come from the same distribution.

**In detail:** A far-out value is not always wrong: it may be a real result, or a failed replicate. Remove one only for a reason you can report (a failed biological replicate, a contaminated well) and say so in the methods. A statistical test only flags candidates. QuantAnalysis never removes values by itself: the original table is never changed, and a cleaned copy has the flagged cells blank so that paired rows stay lined up.

**How it's calculated:** Each column is tested separately and needs at least 3 values.

- ROUT (Q default 1%): a robust fit of the centre μ and a robust SD (RSDR), then each value gets t = |x − μ| / RSDR with a two-tailed P from the t distribution with N − 1 df, and the Benjamini-Hochberg rule at Q decides which are outliers (at most 30% of the values).
- Grubbs' test: G = max|x − x̄| / s; the value is an outlier when G exceeds ((n − 1)/√n) · √(t² / (n − 2 + t²)), with t the upper α/(2n) point of the t distribution with n − 2 df. It finds at most one outlier; the iterative version repeats it, and two similar outliers can mask each other.

**Where you see it:** The **Identify outliers** sheet: rows "Outliers", "Number of outliers", "Total number of outliers", "Robust centre", "Robust SD of residuals (RSDR)", "Critical G".

**Learn more:** [Outlier (Wikipedia)](https://en.wikipedia.org/wiki/Outlier)

**Related:** [[Identify outliers (ROUT)]], [[Grubbs' outlier test]], [[Standard deviation]], [[Median]]

### P value

<!-- aliases: P, p value, P-value, p-value, P values, P value (two-tailed), Is the correlation different from zero?: P value, Is the correlation different from zero?: P value (two-tailed), Is the slope different from zero?: P value, Is the slope different from zero?: P value (two-tailed) -->

**In short:** The probability of getting a result at least as extreme as yours if there were really no effect.

**In detail:** A small P (below your alpha) means the data are unlikely under the null hypothesis, so you reject it. A P of 0.03 does not mean a 3% chance that the null hypothesis is true, and it says nothing about the size or importance of the effect: use the confidence interval for that. A large P means the data do not show an effect, not that there is none; especially with N = 3, power is low. P depends on the test, the sample size and the choice of tails, and it is only valid if the values are independent, which is why N matters.

**How it's calculated:** P is the tail area of the test statistic's distribution under the null hypothesis. For the unpaired t test, P = 2 · P(T_df > |t|). Where an exact P is possible, it is counted from all possible outcomes (see [[Exact and approximate P values]]). A P that rounds to 1 shows as ">0.9999" (>.999 or >0.999 in the APA and NEJM styles); a P below the display precision shows as "<0.0001". Sheets show P with 4 decimals (classic style), or 3 (APA, NEJM) as set in **P value style**.

**Where you see it:** Row "P value" on nearly every results sheet, with "P value summary" (stars) beneath it. Variants: "P value (one tailed)", "P value (two-sided)", and "Is the slope different from zero?: P value".

**Learn more:** [P-value (Wikipedia)](https://en.wikipedia.org/wiki/P-value)

**Related:** [[Alpha]], [[One- and two-tailed P values]], [[Exact and approximate P values]], [[Adjusted P value]], [[Confidence interval]], [[P value summary (stars)]]

### P value summary (stars)

<!-- aliases: P value summary, P value summary (stars), Stars, Asterisks, ns, Summary -->

**In short:** The P value shown as asterisks: ns for not significant, then * ** *** ****, with more stars for smaller P.

**In detail:** Stars are a quick visual code, used on graphs and in the "P value summary" rows. They use fixed cut-offs and do not follow the alpha you set. Report the P value itself, not only the stars. Stars on graphs usually mean a multiple comparison's adjusted P.

**How it's calculated:** Stars are decided on the unrounded P, with ≤ at every cut-off, so P = 0.05000001 shows as 0.0500 but is ns.

| Style | ns | * | ** | *** | **** | P shown as |
|---|---|---|---|---|---|---|
| classic (default) | > 0.05 | ≤ 0.05 | ≤ 0.01 | ≤ 0.001 | ≤ 0.0001 | 0.0123, <0.0001 |
| APA | > 0.05 | ≤ 0.05 | ≤ 0.01 | ≤ 0.001 | no more | .012, <.001 |
| NEJM | > 0.05 | ≤ 0.05 | ≤ 0.01 | ≤ 0.001 | no more | 0.012, <0.001 |

**Where you see it:** Row "P value summary" under each "P value" row, the "summary" of each multiple comparison (from the adjusted P), the choice **P value style** in each dialog, and the stars on graphs.

**Related:** [[P value]], [[Alpha]], [[Adjusted P value]]

### Paired data

<!-- aliases: Paired, Paired data, Pairs, Matched data, Matched pairs, Matching, Number of pairs, Correlation coefficient (r), Correlation coefficient, Spearman correlation (rs), Spearman correlation, Was the pairing significantly effective (P < 0.05)?, Was the pairing significantly effective ?, Is there significant matching (P < 0.05)?, Is there significant matching ?, Individual (between rows), Subject, SD of differences, SEM of differences, Number of subjects -->

**In short:** Values that come in linked sets: each row is the same subject, experiment or sample measured under every condition.

**In detail:** If you treat and image the same cultures on the same day, the control and treated values for one experiment belong together. Pairing removes the experiment-to-experiment variation from the comparison, which makes the test more sensitive when that variation is large. Only use paired tests when the pairing is real (set before the experiment), and never pair after seeing the values. In a paired table, row i of every column is the same subject; for a SuperPlot, tick **Paired** when every replicate was measured in every column.

**How it's calculated:** For two columns, a paired t test uses d_i = B_i − A_i, t = mean(d) / (SD(d)/√n) with df = n − 1. A row with a blank in either column is dropped, and the result reports how many rows were dropped. The Wilcoxon test ranks |d_i|. The effectiveness of pairing is judged by Pearson's r (or Spearman's rs for Wilcoxon) between the columns, with a one-tailed P for r > 0. In repeated-measures ANOVA, "Was the matching effective?" tests F = MS_subjects / MS_residual.

**Where you see it:** The **Paired** setting of a table, "Number of pairs", "Mean difference (B - A)", "SD of differences", "SEM of differences", and the section "How effective was the pairing?" with "Correlation coefficient (r)" and "Was the pairing significantly effective (P < 0.05)?"; "Individual (between rows)" in a repeated-measures ANOVA table.

**Learn more:** [Paired difference test (Wikipedia)](https://en.wikipedia.org/wiki/Paired_difference_test)

**Related:** [[Paired t test]], [[Wilcoxon matched-pairs test]], [[Repeated-measures one-way ANOVA]], [[Friedman test]], [[Biological replicate]]

### Percentiles and quartiles

<!-- aliases: Percentile, Percentiles, Quartile, Quartiles, 10% Percentile, 25% Percentile, 75% Percentile, 90% Percentile, 25th percentile, 75th percentile, Minimum, Maximum, Range, Interquartile range, IQR -->

**In short:** The value below which a given percent of the data lies; the quartiles are the 25th, 50th (median) and 75th percentiles.

**In detail:** The interquartile range, from the 25th to the 75th percentile, holds the middle half of the values. It is the spread that goes with the median, as the SD goes with the mean, and it is not distorted by skew. Percentiles of very small samples are rough: with 3 values the 25th and 75th are just interpolations between neighbours.

**How it's calculated:** Sort the n values. The p-th percentile is at rank (n + 1)p, interpolated linearly between the neighbouring ranks. If the rank falls outside 1…n, the minimum or maximum is used. This is the Hyndman-Fan type 6 method. Other packages often use a different default (type 7), so quartiles of small samples can differ slightly. Example: values 3, 5, 7, 9 (n = 4) have a 25th percentile at rank 1.25, so 3 + 0.25 · (5 − 3) = 3.5.

**Where you see it:** **Column statistics**, rows "Minimum", "10% Percentile", "25% Percentile", "Median", "75% Percentile", "90% Percentile", "Maximum" and "Range".

**Learn more:** [Percentile (Wikipedia)](https://en.wikipedia.org/wiki/Percentile)

**Related:** [[Median]], [[Column statistics]], [[Mean]]

### Pseudoreplication

<!-- aliases: Pseudoreplication, Pseudo-replication, Pseudoreplicate -->

**In short:** Treating repeated measurements of the same sample (many cells or images) as if they were independent samples, which makes P falsely small.

**In detail:** Suppose you image 200 cells from one control dish and 200 from one treated dish. A t test on the 400 cells has 398 df and may give P < 0.0001, but you tested one dish against one dish: N = 1 per group. The cells share the same culture, day and handling, so they are not independent, and the test is asking whether the two dishes differ, not whether the treatment does. The cure is to test one value per biological replicate, or to use a nested analysis that accounts for the replicate level.

**How it's calculated:** There is no formula; it is a design error. QuantAnalysis guards against it by analysing one value per replicate and column when a table has a Replicate column, by marking a table that has one dot per object with a red warning, and by a warning in two-way ANOVA when a cell has more than 30 values. The nested analysis uses the correct error term for cells within replicates.

**Where you see it:** The red warning on the table, its results and graphs, and the warning on the **Two-way ANOVA** sheet.

**Learn more:** [Pseudoreplication (Wikipedia)](https://en.wikipedia.org/wiki/Pseudoreplication)

**Related:** [[N and n]], [[Biological replicate]], [[Technical replicate]], [[Nested t test and nested one-way ANOVA]], [[SuperPlot]], [[Intraclass correlation]]

### R squared

<!-- aliases: R squared, R², R2, R-squared, Eta squared, Partial eta squared, Coefficient of determination, Goodness of fit: R squared, Correlation: R squared, Correlation: rs squared -->

**In short:** The fraction of the total variation that the model (a group, a line, a curve) explains: 0 is nothing, 1 is everything.

**In detail:** An R² of 0.82 in a t test means the group explains 82% of the variation in the values. For a regression line it says how close the points are to the line. R² always rises as you add parameters to a fit, so do not use it alone to pick a model. A high R² does not mean the relationship is causal or that the fit is appropriate. With a line forced through the origin, R² is measured against Σy² and is not comparable with an ordinary R².

**How it's calculated:**

- t test: R² = t² / (t² + df), also called eta squared (partial eta squared for the paired test).
- One-way ANOVA: R² = SS_between / SS_total.
- Repeated measures: treatment R² = SS_treatment / (SS_treatment + SS_residual), and matching R² = SS_subjects / SS_total.
- Test for trend: effect-size R² = SS_trend / SS_total, and alerting R² = SS_trend / SS_between-means.
- Regression: R² = 1 − SS_residual / SS_total.
- Pearson correlation: R² = r².

**Where you see it:** Rows "R squared", "R squared (eta squared)", "R squared (partial eta squared)", the trend rows "R squared (effect size: SS trend / SS total)" and "R squared (alerting: SS trend / SS between means)", "Goodness of fit: R squared" (regression) and "Correlation: R squared".

**Learn more:** [Coefficient of determination (Wikipedia)](https://en.wikipedia.org/wiki/Coefficient_of_determination)

**Related:** [[Effect size]], [[Sum of squares]], [[Linear regression]], [[Correlation (Pearson and Spearman)]], [[Test for linear trend]]

### Ranks and ties

<!-- aliases: Ranks, Rank, Ties, Tied values, Midrank, Midranks, Sum of ranks in A, Sum of ranks, Mann-Whitney U, Sum of signed ranks (W), Sum of signed ranks, Sum of positive ranks, Sum of negative ranks, Zero differences, Mean rank, Rank sum, Kruskal-Wallis statistic, Friedman statistic -->

**In short:** Rank tests replace each value with its position in the sorted data, so only the order matters, not the exact size of the numbers.

**In detail:** Ranking makes a test insensitive to outliers and to the shape of the distribution, which is why Mann-Whitney, Kruskal-Wallis, Wilcoxon and Friedman do not assume a Gaussian distribution. The price is power with small samples: with exact P values there is a smallest possible P, and a Mann-Whitney test at 3 vs. 3 can never go below 0.10. Tied values share the average of the ranks they would have taken, and each test then adjusts its variance for the ties.

**How it's calculated:** Rank all values together (Mann-Whitney, Kruskal-Wallis), or within each row (Friedman), or rank the absolute paired differences (Wilcoxon). Example: the values 4, 7, 7, 9 have ranks 1, 2.5, 2.5, 4. For two groups U_B = R_B − n_B(n_B + 1)/2, the number of (A, B) pairs where B is larger (a tie counts one half). In the Wilcoxon test, W = (sum of positive ranks) − (sum of negative ranks); zero differences are dropped by default ("exclude"), or ranked and then left out of the sums if Pratt's method is chosen. The tie correction subtracts Σ(t³ − t) terms, with t the size of each group of ties.

**Where you see it:** Rows "Sum of ranks in A, B" and "Mann-Whitney U" (Mann-Whitney), "Sum of signed ranks (W)", "Sum of positive ranks", "Sum of negative ranks" and "Zero differences" (Wilcoxon), and the rank sums and mean ranks of Kruskal-Wallis and Friedman.

**Learn more:** [Ranking (Wikipedia)](https://en.wikipedia.org/wiki/Ranking_(statistics))

**Related:** [[Mann-Whitney test]], [[Wilcoxon matched-pairs test]], [[Kruskal-Wallis test]], [[Friedman test]], [[Exact and approximate P values]]

### Relative risk

<!-- aliases: Relative risk, Risk ratio, RR, Difference of proportions (Treated − Control), Difference of proportions, Number needed to treat (1 / |difference|), Number needed to treat, NNT -->

**In short:** The probability of an outcome in one group divided by its probability in the other: 1 means no difference.

**In detail:** If 8 of 10 treated cells die and 3 of 10 controls die, the risks are 0.8 and 0.3, so the relative risk is 2.67: treated cells are 2.67 times as likely to die. This is easier to read than an odds ratio, but it requires that the proportions in your table are real proportions of the groups (not set by the sampling, as in case-control studies). The sheet also gives the difference of proportions and the number needed to treat, which is 1 / |difference|.

**How it's calculated:** With a, b the group of interest with and without the outcome, and c, d the other group:

    RR = (a / (a + b)) / (c / (c + d))

The confidence interval uses Katz's log method: exp(ln RR ± z · √(1/a − 1/(a + b) + 1/c − 1/(c + d))). When a or c is 0, 0.5 is added to every cell (the sheet says so). The difference of proportions has Newcombe's hybrid score interval (built from the two Wilson intervals); the interval of the NNT is the reciprocals of the limits, and when the interval of the difference includes 0 the NNT interval runs through infinity.

**Where you see it:** The **Contingency** sheet, rows "Relative risk (Treated vs Control, Died)", "Difference of proportions (Treated − Control)" and "Number needed to treat (1 / |difference|)".

**Learn more:** [Relative risk (Wikipedia)](https://en.wikipedia.org/wiki/Relative_risk)

**Related:** [[Odds ratio]], [[Contingency tables (Fisher's and chi-square)]], [[Hazard ratio]], [[Confidence interval]]

### Residual

<!-- aliases: Residual, Residuals, Error, Residual (within columns), Residual (random), Residual (within replicates), Sy.x, Goodness of fit: Sy.x, Residual error -->

**In short:** What is left over after a model is fitted: the difference between a measured value and the value the model predicts.

**In detail:** In ANOVA, the residual of a value is its distance from its own group mean; in regression, its vertical distance from the line or curve. The residuals show how well the model fits, and their size sets the noise that effects must beat. Tests assume the residuals are roughly Gaussian with the same SD in every group. The residual degrees of freedom are the number of values minus the number of fitted quantities. In nested designs, "within replicates" is the residual.

**How it's calculated:** residual = y − ŷ. Residual SS = Σ(y − ŷ)², the residual mean square is SS_residual / df_residual, and Sy.x = √(SS_residual / df) is the standard deviation of the residuals (for a line, df = n − 2). In ordinary one-way ANOVA, df_residual = N − k.

**Where you see it:** The ANOVA table lines "Residual (within columns)", "Residual (random)", "Residual (within replicates)" and "Residual" (two-way), and "Goodness of fit: Sy.x" in regressions. In the mixed-effects model, "Residual: SD" and "Residual: variance" are the error variance.

**Related:** [[Sum of squares]], [[Mean square]], [[Least squares]], [[Variance components]], [[Standard deviation]]

### SEM

<!-- aliases: SEM, Std. Error of Mean, Standard error of the mean, SEM of discrepancy, S.E.M. -->

**In short:** The standard error of the mean: how precisely the sample mean estimates the true mean.

**In detail:** SEM shrinks as N grows, so it measures the precision of the mean, not the spread of the data. Use the SD to show the spread, and the SEM or a 95% confidence interval to show how repeatable the mean is. Always say in the legend which one the error bars show, and what N is. With N = 3, an SEM bar covers only about 58% of the possible true means, so a 95% CI (4.30 SEM on each side) is more honest.

**How it's calculated:** SEM = s / √n, with n the number of values in the column (for a SuperPlot, the number of biological replicates). It needs n ≥ 2. The 95% CI of the mean is x̄ ± t(0.975, n − 1) · SEM.

**Where you see it:** **Column statistics** (row "Std. Error of Mean"), the group summary of ANOVA sheets ("SEM = …"), "SEM of differences" in paired tests, and the SEM error-bar option of graphs.

**Learn more:** [Standard error of the mean (Handbook of Biological Statistics)](https://www.biostathandbook.com/standarderror.html)

**Related:** [[Standard error]], [[Standard deviation]], [[Confidence interval]], [[N and n]], [[Mean]]

### Sensitivity and specificity

<!-- aliases: Sensitivity, Specificity, Sensitivity and specificity, Positive predictive value, Negative predictive value, Predictive value, Predictive values, Best cutoff, Positive when, Layout -->

**In short:** Sensitivity is the share of true positives the test catches; specificity is the share of true negatives it correctly clears.

**In detail:** A marker that flags 90 of 100 responding cells has a sensitivity of 90%. If it also (wrongly) flags 20 of 100 non-responding cells, its specificity is 80%. Raising the cutoff trades one for the other, which is what a ROC curve shows. The positive and negative predictive values say how often a positive or negative result is right, but they hold only at the prevalence of your table. The best cutoff in a ROC analysis maximises Youden's J = sensitivity + specificity − 1.

**How it's calculated:** With a table of test result (rows) by condition (columns): a = true positives, b = false positives, c = false negatives, d = true negatives. Sensitivity = a / (a + c), specificity = d / (b + d), positive predictive value = a / (a + b), negative predictive value = d / (c + d). Each has a Wilson score confidence interval: with p the proportion and z the normal quantile, (p + z²/2n ± z · √(p(1 − p)/n + z²/4n²)) / (1 + z²/n).

**Where you see it:** The **Contingency** sheet with **Diagnostic test** ticked (rows "Sensitivity", "Specificity", "Positive predictive value", "Negative predictive value"), and the cutoff table and the section "Best cutoff (Youden's J)" of the **ROC curve** sheet.

**Learn more:** [Sensitivity and specificity (Wikipedia)](https://en.wikipedia.org/wiki/Sensitivity_and_specificity)

**Related:** [[ROC curve]], [[Area under the curve (AUC)]], [[Likelihood ratio]], [[Contingency tables (Fisher's and chi-square)]]

### Sphericity

<!-- aliases: Sphericity, Assume sphericity (equal variability of differences)?, Assume sphericity ?, Assume sphericity, Compound symmetry -->

**In short:** An assumption of repeated-measures ANOVA: the differences between every pair of conditions must have the same variance.

**In detail:** If you measure each replicate at 0, 1 and 24 hours, the difference between 0 and 1 hour may vary a lot less than the difference between 0 and 24 hours. Then sphericity is violated, the ordinary F test is too liberal and P is too small. The correction is to multiply the degrees of freedom by the Geisser-Greenhouse epsilon. With only two conditions there is just one difference, so sphericity cannot be violated.

**How it's calculated:** Sphericity means all differences between pairs of columns have equal variance. Its departure is measured by the [[Geisser-Greenhouse epsilon]]: 1 means sphericity holds, and the lower bound is 1/(k − 1). With the correction on (the default, "Don't assume sphericity"), each multiple comparison uses only the two columns being compared (essentially a paired t test: SE = SD of the differences / √n, df = n − 1). With sphericity assumed, they use the pooled residual mean square of all columns, with (n − 1)(k − 1) df.

**Where you see it:** The row "Assume sphericity (equal variability of differences)?" on the **Repeated-measures** and **Mixed-effects** sheets, and the dialog checkbox "Don't assume sphericity (Geisser-Greenhouse)".

**Related:** [[Geisser-Greenhouse epsilon]], [[Repeated-measures one-way ANOVA]], [[Mixed-effects model]], [[Paired data]]

### Standard deviation

<!-- aliases: SD, Std. Deviation, Standard deviation, SD of bias, SD of discrepancy, Subject: SD, Residual: SD, Sample standard deviation, Lower limit, Upper limit -->

**In short:** A measure of how far the values typically lie from their mean.

**In detail:** The SD describes the spread of your data, not the uncertainty of the mean. Adding more replicates does not make it smaller; it only makes it more reliable. About 95% of Gaussian data lie within 1.96 SD of the mean. In a SuperPlot, the SD of the replicates shows how much the replicates vary. Do not use SD for N = 2 or very small samples as a summary of a distribution; it is a very rough estimate.

**How it's calculated:** s = √( Σ(x − x̄)² / (n − 1) ). It uses n − 1 (the sample SD) because the mean was estimated from the same data. It needs n ≥ 2. The pooled SD of several groups is the square root of their pooled variance.

**Where you see it:** **Column statistics** (row "Std. Deviation"), the group summary of ANOVA sheets ("SD = …"), "SD of differences" and "SD of bias" (Bland-Altman), and the SD error-bar option of graphs.

**Learn more:** [Standard deviation (Handbook of Biological Statistics)](https://www.biostathandbook.com/dispersion.html)

**Related:** [[Variance]], [[Standard error]], [[SEM]], [[Coefficient of variation]], [[Mean]]

### Standard error

<!-- aliases: SE, Standard error, Standard errors, Std. Error, Std. Error: Slope, Std. Error: Y-intercept, Std. Error: Intercept, Std. Error: Km, Std. Error: Vmax, Difference between means (B - A) ± SEM, Difference between means (T - C) ± SEM, Difference between means ± SEM -->

**In short:** The standard deviation of an estimate (a mean, a difference, a slope) over repeated experiments: how much it would wobble if you repeated the study.

**In detail:** Every estimate has a standard error. It gets smaller with more independent samples, and larger with more variable data. The standard error of the mean is the best known, but the same idea applies to the difference between two means (the denominator of the t ratio), to a slope or to a fitted curve parameter. A confidence interval is the estimate ± a t multiplier times its standard error.

**How it's calculated:** Examples used by this engine:

- Mean: SE = s / √n.
- Unpaired t test: SE = s_p · √(1/n_A + 1/n_B). Welch's: SE = √(s_A²/n_A + s_B²/n_B). Paired: SE = SD(d) / √n.
- Linear regression: SE(a) = Sy.x / √Σ(x − x̄)², SE(b) = Sy.x · √(1/n + x̄² / Σ(x − x̄)²).
- Nonlinear regression: from the covariance Sy.x² (JᵀJ)⁻¹ (asymptotic).
- ANOVA multiple comparisons: SE = √(MS_residual (1/nᵢ + 1/nⱼ)).

**Where you see it:** Rows "Difference between means (B - A) ± SEM" (the number after ± is the SE of the difference), "Std. Error: Slope", "Std. Error: Y-intercept" and "Std. Error: …" of curve fits, "Std. Error" (ROC area), and the "SE" in a multiple comparison row.

**Learn more:** [Standard error (Wikipedia)](https://en.wikipedia.org/wiki/Standard_error)

**Related:** [[SEM]], [[Standard deviation]], [[Confidence interval]], [[t ratio]], [[Degrees of freedom]]

### Sum of squares

<!-- aliases: SS, Sum of squares, Sums of squares, Treatment (between columns), Treatment, Between groups (columns), Between groups, Among replicates (within columns), Total -->

**In short:** The total of the squared distances of values from a reference value; ANOVA splits the total into parts.

**In detail:** The sum of squares measures variation. ANOVA divides the total variation into the part explained by your factor and the part left over (the residual). If the groups differ, a large part of the total is explained by the grouping. The parts always add up for a one-way ANOVA: SS_total = SS_between + SS_within. In unbalanced two-way designs, the Type III sums of squares for each effect no longer add up to the total.

**How it's calculated:** For ordinary one-way ANOVA, with k groups, group means ȳᵢ, grand mean ȳ and N values:

    SS_between = Σ nᵢ (ȳᵢ − ȳ)²        (k − 1 df)
    SS_within = Σᵢ Σⱼ (yᵢⱼ − ȳᵢ)²       (N − k df)
    SS_total = SS_between + SS_within

Repeated measures also separates the subjects: SS_total = SS_treatment + SS_subjects + SS_residual. The nested analysis separates "Between groups", "Among replicates within groups" and "Within replicates (residual)".

**Where you see it:** The "SS" of each line of an ANOVA table ("Treatment (between columns)", "Residual (within columns)", "Total"; "Individual (between rows)" for repeated measures; "Between groups (columns)" and "Among replicates (within columns)" for nested), and "Goodness of fit: Sum of squares" for curve fits.

**Learn more:** [Partition of sums of squares (Wikipedia)](https://en.wikipedia.org/wiki/Partition_of_sums_of_squares)

**Related:** [[Mean square]], [[F ratio]], [[Degrees of freedom]], [[R squared]], [[Residual]], [[Ordinary one-way ANOVA]]

### t ratio

<!-- aliases: t, t ratio, t statistic, t value, Student's t -->

**In short:** The difference you measured divided by its standard error: how many standard errors your result is away from zero.

**In detail:** A t of 3.7 means the difference is 3.7 standard errors from 0. The larger |t| is, the smaller the P. To turn a t into a P you need its degrees of freedom, because the t distribution has fatter tails with few df (with 4 df, |t| must exceed 2.78 to reach P < 0.05, but only 1.96 with infinite df).

**How it's calculated:** t = estimate / SE. For the unpaired t test, t = (x̄_B − x̄_A) / (s_p · √(1/n_A + 1/n_B)) with df = n_A + n_B − 2. For paired data, t = mean(d) / (SD(d)/√n) with df = n − 1. For a one-sample test, t = (x̄ − μ₀) / SEM. P = 2 · P(T_df > |t|) (two-tailed). In a multiple comparison, t = difference / SE. The confidence interval uses the t quantile at 1 − (1 − level)/2.

**Where you see it:** Row "t, df" (for example "t=3.674, df=4") on the t test sheets, the nested t test, and the "t" of the one-sample test in Column statistics. Also the "statistic" of multiple comparison rows.

**Learn more:** [Student's t-test (Wikipedia)](https://en.wikipedia.org/wiki/Student%27s_t-test)

**Related:** [[Degrees of freedom]], [[Standard error]], [[P value]], [[Unpaired t test]], [[Paired t test]], [[F ratio]]

### Technical replicate

<!-- aliases: Technical replicates, Technical replicate, Repeated measurement, Repeated measurements, n (technical) -->

**In short:** A repeated measurement of the same sample: its images, fields, wells or cells. The count is n.

**In detail:** Technical replicates tell you how precisely one sample was measured, not whether a condition has an effect. They share the biology and handling of their sample, so they are not independent. Their value is that they reduce measurement noise: averaging several fields gives a better estimate of that sample's value. Average them (or pool them as described in [[N and n]]) before testing. Dots of a SuperPlot are the technical replicates; the large markers are the biological replicates.

**How it's calculated:** In a table with replicates, a technical replicate is any value within a replicate block; the analyses use the replicate's mean or median. In the nested analyses, technical replicates are the n_gr values y_grj of replicate r in group g, and their variation is the "within replicates" (residual) variance.

**Where you see it:** The small dots of a SuperPlot, the n in "N = … replicates; n = … values" on the **Nested** sheet, and the choice **Dot =** an image or object in the **Import CellQuant results** dialog.

**Related:** [[Biological replicate]], [[N and n]], [[Pseudoreplication]], [[SuperPlot]], [[Variance components]]

### Variance

<!-- aliases: Variance, Variances, Residual: variance, Subject: variance, Sample variance -->

**In short:** The average squared distance of the values from their mean; the SD is its square root.

**In detail:** Variance is the quantity that adds up: the variances of independent sources of variation can be summed, which is why ANOVA and variance components work with variances and mean squares, not SDs. It is in squared units (a cell area in µm² gives a variance in µm⁴), which is why the SD is used to describe data. Tests that assume "equal SDs" are tests about equal variances.

**How it's calculated:** s² = Σ(x − x̄)² / (n − 1). The pooled variance of two groups is s_p² = ((n_A − 1)s_A² + (n_B − 1)s_B²) / (n_A + n_B − 2). The F test of equal variances is the ratio of the larger to the smaller s² (see [[F ratio]]), and Bartlett's and Brown-Forsythe tests compare variances of more groups.

**Where you see it:** The "Subject: variance" and "Residual: variance" rows of the **Mixed-effects** sheet, the "F test to compare variances" section of the **Unpaired t test**, the Brown-Forsythe and Bartlett sections of the **Ordinary one-way ANOVA**, and the variance components of the **Nested** sheet.

**Related:** [[Standard deviation]], [[Mean square]], [[Variance components]], [[F ratio]]

### Variance components

<!-- aliases: Variance components, Variance component, Among replicates (σ² replicates), Among replicates, Within replicates (σ² residual), Within replicates, σ² replicates, σ² residual -->

**In short:** The total variation split into the part that comes from differences among replicates and the part within replicates.

**In detail:** If 600 cells come from three experiments, some of the variation among cells is caused by the experiments (day, batch, passage) and some by cell-to-cell differences within an experiment. Variance components say how much comes from each source. When the replicate variance is large compared with the within-replicate variance, cells carry little independent information, and the nested analysis uses replicates, not cells, for the test.

**How it's calculated:** From the nested ANOVA table (method of moments): E(MS_resid) = σ², E(MS_rep) = σ² + c_r σ²_rep. So σ̂²_rep = max(0, (MS_rep − MS_resid) / c_r) (a note says when it is set to 0) and σ̂²_resid = MS_resid. The mixed-effects model estimates the subject and residual variances by REML instead.

**Where you see it:** The **Nested** sheet, section "Variance components": rows "Among replicates (σ² replicates)", "Within replicates (σ² residual)" and the intraclass correlation. The **Mixed-effects** sheet: "Subject: SD", "Subject: variance", "Residual: SD", "Residual: variance".

**Learn more:** [Nested ANOVA (Handbook of Biological Statistics)](https://www.biostathandbook.com/nestedanova.html)

**Related:** [[Intraclass correlation]], [[Fixed and random effects]], [[Nested t test and nested one-way ANOVA]], [[Mixed-effects model]], [[Variance]]
