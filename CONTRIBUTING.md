# Contributing

Thanks for your interest in contributing to resume-site.

## Getting Started

1. Fork the repo and clone your fork
2. Create a branch: `git checkout -b my-feature`
3. Set up a local dev environment:
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   pip install pytest flake8
   cp config.example.yaml config.yaml
   python manage.py init-db
   ```
4. Make your changes
5. Run the tests: `pytest -v`
6. Run the linter: `flake8 . --max-line-length=120`
7. Commit with a clear message and open a pull request

## Guidelines

- Keep PRs focused on one change. Smaller is easier to review.
- Add or update tests when you add or change functionality.
- Follow existing code style (PEP 8, 120 char line length).
- Don't commit `config.yaml`, database files, or personal photos — these are gitignored for a reason.

## Reporting Bugs

Open an issue using the **Bug Report** template. Include steps to reproduce, what you expected, and what actually happened.

## Suggesting Features

Open an issue using the **Feature Request** template. Describe the use case, not just the solution.

## Questions

Open a regular issue. There are no dumb questions.
