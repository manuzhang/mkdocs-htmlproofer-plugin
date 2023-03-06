# mkdocs-htmlproofer-plugin [![PyPI - Version](https://img.shields.io/pypi/v/mkdocs-htmlproofer-plugin.svg)](https://pypi.org/project/mkdocs-htmlproofer-plugin)

[![GitHub Actions](https://github.com/manuzhang/mkdocs-htmlproofer-plugin/actions/workflows/ci.yml/badge.svg)](https://github.com/manuzhang/mkdocs-htmlproofer-plugin/actions/workflows/ci.yml)

*A [MkDocs](https://www.mkdocs.org/) plugin that validates URLs, including anchors, in rendered html files*.

## Installation

0. Prerequisites

* Python >= 3.6
* MkDocs >= 0.17

1. Install the package with pip:

```bash
pip install mkdocs-htmlproofer-plugin
```

2. Enable the plugin in your `mkdocs.yml`:

> **Note:** If you have no `plugins` entry in your config file yet, you'll likely also want to add the `search` plugin.
MkDocs enables it by default if there is no `plugins` entry set, but now you have to enable it explicitly.

```yaml
plugins:
    - search
    - htmlproofer
```

To enable cross-page anchor validation, you must set `use_directory_urls = False` in `mkdocs.yml`:

```yaml
use_directory_urls: False
```

## Configuring

### `enabled`

True by default, allows toggling whether the plugin is enabled.
Useful for local development where you may want faster build times.

```yaml
plugins:
  - htmlproofer:
      enabled: !ENV [ENABLED_HTMLPROOFER, True]
```

Which enables you do disable the plugin locally using:

```bash
export ENABLED_HTMLPROOFER=false
mkdocs serve
```


### `raise_error`

Optionally, you may raise an error and fail the build on first bad url status. Takes precedense over `raise_error_after_finish`.

```yaml
plugins:
  - htmlproofer:
      raise_error: True
```

### `raise_error_after_finish`

Optionally, you may want to raise an error and fail the build on at least one bad url status after all links have been checked.

```yaml
plugins:
  - htmlproofer:
      raise_error_after_finish: True
```

### `raise_error_excludes`

When specifying `raise_error: True` or `raise_error_after_finish: True`, it is possible to ignore errors
for combinations of urls (`'*'` means all urls) and status codes with `raise_error_excludes`.

```yaml
plugins:
  - search
  - htmlproofer:
      raise_error: True
      raise_error_excludes:
        504: ['https://www.mkdocs.org/']
        404: ['https://github.com/manuzhang/mkdocs-htmlproofer-plugin']
        400: ['*']
```

### `validate_external_urls`

Avoids validating any external URLs (i.e those starting with http:// or https://).
This will be faster if you just want to validate local anchors, as it does not make any network requests.

```yaml
plugins:
  - htmlproofer:
      validate_external_urls: False
```

### `validate_rendered_template`

Validates the entire rendered template for each page - including the navigation, header, footer, etc.
This defaults to off because it is much slower and often redundant to repeat for every single page.

```yaml
plugins:
  - htmlproofer:
      validate_rendered_template: True
```

## Improving

More information about plugins in the [MkDocs documentation](http://www.mkdocs.org/user-guide/plugins/)

## Acknowledgement

This work is based on the [mkdocs-markdownextradata-plugin](https://github.com/rosscdh/mkdocs-markdownextradata-plugin) project and the [Finding and Fixing Website Link Rot with Python, BeautifulSoup and Requests](https://www.twilio.com/blog/2018/07/find-fix-website-link-rot-python-beautifulsoup-requests.html) article. 
