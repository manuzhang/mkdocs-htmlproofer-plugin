# Contribution Guidelines

Thank you for considering to contribute to this project. These guidelines will help you get going with development and outline the most important rules to follow when submitting pull requests for this project.

<br/>

## Development

### Setup

##### Prerequisites

- [Python 3]
- [virtualenv]

##### Steps

1. Clone the (forked) repository
1. Create a virtualenv with `virtualenv env`
1. Activate virtualenv `source env/bin/activate` or `env/Scripts/activate` on Windows
1. Run `pip install -r dev-requirements.txt` in the project directory
1. Run `pip install .` in the project directory

#### Running the Linter

```bash
flake8 htmlproofer/ tests/ setup.py --count --select=E9,F63,F7,F82 --show-source --statistics
flake8 htmlproofer/ tests/ setup.py --count --max-complexity=10 --max-line-length=127 --statistic
```

#### Checking for Type Errors

```bash
mypy htmlproofer
```

#### Check Import Ordering

```bash
isort --check .
```

#### Running Unit Tests

```bash
pytest tests/unit
```

#### Running Integration Tests

Without directory urls:

```bash
cd tests/integration
mkdocs build
```

With directory urls:

```bash
cd tests/integration
mkdocs build --use-directory-urls
```

<br/>

## Submitting Changes

To get changes merged, create a pull request. Here are a few things to pay attention to when doing so:

#### Commit Messages

The summary of a commit should be concise and worded in an imperative mood.
...a *what* mood? This should clear things up: *[How to Write a Git Commit Message][git-commit-message]*

#### Code Style

Make sure your code follows [PEP-8](https://www.python.org/dev/peps/pep-0008/) and keeps things consistent with the rest of the code.

#### Tests

If it makes sense, writing tests for your PRs is always appreciated and will help get them merged.

[Python 3]: https://www.python.org/
[virtualenv]: https://virtualenv.pypa.io/
[git-commit-message]: https://chris.beams.io/posts/git-commit/
