# Contributing to pagerank-graph

Thank you for helping improve the tool. This guide explains how to install it for development, run the tests
and propose a change.

## Install for development

Python 3.11 or later.

```bash
git clone https://github.com/J2K0000/pagerank-graph.git
cd pagerank-graph
pip install -e ".[test,google]"
python -m pytest -q
```

The editable install (`-e`) picks up your changes without reinstalling. After a version bump in
`prgraph/__init__.py`, run `pip install -e .` again: the installed metadata does not follow on its own (a test
checks it).

## Before proposing a change

1. **One test for every fix or feature.** The suite (`python -m pytest -q`) must pass. Tests run automatically
   on GitHub, on Linux and Windows, for Python 3.11, 3.13 and 3.14.
2. **Synthetic test data only.** Never a crawl, a Search Console export or the name of a real site in the
   repository: not in tests, not in examples, not in comments. `prgraph demo` generates a complete fictitious
   site; tests build their own small graphs (`https://s.fr/...`).
3. **Version and changelog.** Any change to the computation or to what the outputs show bumps the version
   (`prgraph/__init__.py`, SemVer numbering) and adds an entry to `CHANGELOG.md`. An analysis quotes the
   version that produced it: without that, it cannot be reproduced.
4. **The tool speaks French.** Texts it produces (report, messages, Markdown outputs) are in French: short
   sentences, no em dash (U+2014) in generated texts (tests check this). Documentation is in English.
5. **Security.** Any data coming from a crawled site (title, anchor, URL) is treated as hostile: escaped in
   the HTML report, neutralised in the CSVs and the workbook. The local server listens on 127.0.0.1 only and
   requires a token on every action. Report a vulnerability privately: see `SECURITY.md`.

## Proposing the change

Open a pull request against `main` with: what changes, why, and how you checked it (tests, a screenshot of
the report if the display changes). For a significant change (new computation, new input format), open an
issue first to discuss it.

Code entry points: `prgraph/loaders.py` (input formats), `prgraph/clean.py` (graph clean-up),
`prgraph/weights.py` (weightings), `prgraph/pagerank.py` (computation), `prgraph/analysis.py` (diagnostics),
`prgraph/actions.py` (action plan), `prgraph/export.py` (outputs), `prgraph/viewer/` (HTML report),
`prgraph/workbook.py` (Excel workbook). The design note is in `docs/design.md`.

## License

By contributing, you agree that your contribution is published under the project's MIT license (`LICENSE`).
