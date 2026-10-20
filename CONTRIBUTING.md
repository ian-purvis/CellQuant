# Contributing to CellQuant

Thanks for helping. CellQuant is maintained by [@ian-purvis](https://github.com/ian-purvis), who merges every pull request.

## Reporting a problem

[Open an issue](https://github.com/ian-purvis/CellQuant/issues/new/choose) and fill in the form. The most useful reports say:

- your operating system and how you installed CellQuant (the installer, or by hand);
- the CellQuant version (`cellquant/__version__.py`, or the title of the release you downloaded);
- what kind of image you used (microscope, channels, Z slices, size), ideally a real `.nd2` or a small crop of one;
- what you did, what you expected, and what happened, with any red message box text or the end of `cellquant_developer.log` (see [Start here](docs/START_HERE.md#when-something-looks-wrong)).

Please do not attach images that are not yours to share.

## Proposing a change

1. For anything bigger than a small fix, open an issue first so we can agree on the idea.
2. Branch from the latest `main` and keep the change focused on one thing.
3. Run the tests: `python -m pytest tests -q` (see [tests/README.md](tests/README.md) and [docs/DEVELOPERS.md](docs/DEVELOPERS.md)).
4. Open a pull request with a few plain lines: what changed, why, and how you checked it.

## Design rules

napari is only for viewing and drawing; every function lives in CellQuant's own windows. Labels are short, with the explanation in a tooltip. See [docs/DEVELOPERS.md](docs/DEVELOPERS.md) for the repository map.

By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md). Contributions are under the [MIT license](LICENSE).
