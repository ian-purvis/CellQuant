"""Correlation matrix and principal component analysis, checked against pandas, scipy and numpy."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cellquant.errors import StatisticsError
from cellquant.stats import correlation_matrix as exported_matrix
from cellquant.stats import pca as exported_pca
from cellquant.stats.multivariate import correlation_matrix, pca

RNG = np.random.default_rng(0)
_Z = RNG.normal(size=(40, 2))
DATA = np.c_[_Z[:, 0], _Z[:, 0] * 0.8 + RNG.normal(0, 0.6, 40), _Z[:, 1], _Z[:, 1] * 0.5 + RNG.normal(0, 3, 40) + 10]
DATA[3, 1] = np.nan
VARIABLES = {f"V{i + 1}": DATA[:, i] for i in range(4)}


def test_exported_from_package():
    assert exported_matrix is correlation_matrix and exported_pca is pca


def test_pairwise_matrix_matches_pandas():
    result = correlation_matrix(VARIABLES)
    frame = pd.DataFrame(DATA)
    assert np.allclose(result.values["r"], frame.corr().to_numpy())
    assert result.values["n"][0, 1] == 39 and result.values["n"][0, 2] == 40
    assert np.allclose(correlation_matrix(VARIABLES, method="spearman").values["r"], frame.corr("spearman").to_numpy())
    assert any("n differs between pairs" in w for w in result.warnings)


def test_listwise_and_adjustment():
    listwise = correlation_matrix(VARIABLES, missing="listwise")
    assert (listwise.values["n"][np.triu_indices(4, 1)] == 39).all()
    raw = correlation_matrix(VARIABLES)
    holm = correlation_matrix(VARIABLES, adjust="holm")
    pairs = np.triu_indices(4, 1)
    p = raw.values["p"][pairs]
    order = np.argsort(p)
    expected = np.maximum.accumulate(np.minimum(1, p[order] * (6 - np.arange(6))))
    assert holm.values["p_adjusted"][pairs][order] == pytest.approx(expected)
    assert "Holm" in holm.methods and not any("expected by chance" in w for w in holm.warnings)


def test_pca_matches_eigen_decomposition():
    result = pca(VARIABLES, select="number", number=2)
    complete = DATA[np.isfinite(DATA).all(axis=1)]
    eigenvalues = np.sort(np.linalg.eigvalsh(np.corrcoef(complete.T)))[::-1]
    assert result.values["eigenvalues"] == pytest.approx(eigenvalues)
    assert result.values["share"].sum() == pytest.approx(1)
    z = (complete - complete.mean(axis=0)) / complete.std(axis=0, ddof=1)
    u, s, vt = np.linalg.svd(z, full_matrices=False)
    scores = result.values["scores"][["PC1", "PC2"]].to_numpy()
    assert np.abs(scores) == pytest.approx(np.abs(u[:, :2] * s[:2]))
    loadings = result.values["loadings"][:, :2]
    for c in range(2):
        assert np.corrcoef(z @ result.values["vectors"][:, c], complete[:, 0])[0, 1] == pytest.approx(loadings[0, c])
    assert result.values["keep"] == 2 and any("left out" in w for w in result.warnings)
    assert result.values["scores"]["row"].tolist()[:4] == [1, 2, 3, 5]


def test_pca_selection_rules():
    parallel = pca(VARIABLES)
    assert parallel.values["keep"] == 1 and parallel.values["threshold"] is not None
    assert pca(VARIABLES).values["threshold"] == pytest.approx(parallel.values["threshold"])  # fixed seed
    assert pca(VARIABLES, select="kaiser").values["keep"] == int((parallel.values["eigenvalues"] > 1).sum())
    assert pca(VARIABLES, select="variance", variance=0.9).values["keep"] == 3
    covariance = pca(VARIABLES, standardize=False, select="number", number=1)
    complete = DATA[np.isfinite(DATA).all(axis=1)]
    assert covariance.values["eigenvalues"][0] == pytest.approx(np.linalg.eigvalsh(np.cov(complete.T))[-1])


def test_refusals():
    with pytest.raises(StatisticsError, match="at least two variables"):
        pca({"A": [1, 2, 3]})
    with pytest.raises(StatisticsError, match="does not vary"):
        pca({"A": [1, 2, 3, 4], "B": [5, 5, 5, 5]})
    with pytest.raises(StatisticsError, match="whole number"):
        pca(VARIABLES, select="number", number=9)
    with pytest.raises(StatisticsError, match="pairwise"):
        correlation_matrix(VARIABLES, missing="some")
