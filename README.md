# mkdocs-htmlproofer-plugin [![PyPI - Version](https://img.shields.io/pypi/v/mkdocs-htmlproofer-plugin.svg)](https://pypi.org/project/mkdocs-htmlproofer-plugin)

[![GitHub Actions](https://github.com/manuzhang/mkdocs-htmlproofer-plugin/actions/workflows/ci.yml/badge.svg)](https://github.com/manuzhang/mkdocs-htmlproofer-plugin/actions/workflows/ci.yml)

*A [MkDocs](https://www.mkdocs.org/) plugin that validates URLs, including anchors, in rendered html files*.

> [!NOTE]
> [MkDocs 1.6+ supports native validation of anchors](https://www.mkdocs.org/user-guide/configuration/#validation).

## Installation

0. Prerequisites

* Python >= 3.10
* MkDocs >= 1.4.0

1. Install the package with pip:

```bash
pip install mkdocs-htmlproofer-plugin
```

2. Enable the plugin in your `mkdocs.yml`:

> [!NOTE]
> If you have no `plugins` entry in your config file yet, you'll likely also want to add the `search` plugin.
MkDocs enables it by default if there is no `plugins` entry set, but now you have to enable it explicitly.

```yaml
plugins:
    - search
    - htmlproofer
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

Which enables you to disable the plugin locally using:

```bash
export ENABLED_HTMLPROOFER=false
mkdocs serve
```


### `raise_error`

Optionally, you may raise an error and fail the build on first bad url status. Takes precedence over `raise_error_after_finish`.

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
for combinations of URLs and status codes with `raise_error_excludes`. Each URL supports unix style wildcards `*`, `[]`, `?`, etc.

```yaml
plugins:
  - search
  - htmlproofer:
      raise_error: True
      raise_error_excludes:
        504: ['https://www.mkdocs.org/']
        404: ['https://github.com/manuzhang/*']
        400: ['*']
        -1: ['https://flaky.example.com/*']
```

A URL which couldn't be requested at all, such as one whose host wouldn't resolve or which redirected
too many times, is reported as `-1` and excluded under that status. A request which times out is
reported as `504`.

### `ignore_urls`

Avoid validating the given list of URLs by ignoring them altogether. Each URL in the
list supports unix style wildcards `*`, `[]`, `?`, etc.

Unlike `raise_error_excludes`, ignored URLs will not be fetched at all.

```yaml
plugins:
  - search
  - htmlproofer:
      raise_error: True
      ignore_urls:
        - https://github.com/myprivateorg/*
        - https://app.dynamic-service-of-some-kind.io*
```

### `warn_on_ignored_urls`

Log a warning when ignoring URLs with `ignore_urls` option. Defaults to `false` (no warning).

```yaml
plugins:
  - search
  - htmlproofer:
      raise_error: True
      ignore_urls:
        - https://github.com/myprivateorg/*
        - https://app.dynamic-service-of-some-kind.io*
      warn_on_ignored_urls: true
```

### `ignore_pages`

Avoid validating the URLs on the given list of markdown pages by ignoring them altogether.
Each page in the list supports unix style wildcards `*`, `[]`, `?`, etc.

```yaml
plugins:
  - search
  - htmlproofer:
      raise_error: True
      ignore_pages:
        - path/to/file.md
        - path/to/folder/*
```

### `validate_external_urls`

Enabled by default. Set it to `False` to validate local links and anchors without making any
network requests. Disable external checks when building documentation from untrusted contributors
if outbound requests are not needed; enforce build-host egress restrictions as additional protection.

External checks only connect to public IPv4/IPv6 addresses by default. All resolved addresses must
be public, including on redirect hops. Private, loopback, link-local, multicast, reserved, and
translation/tunnel destinations are rejected as `-1`. Localhost links previously skipped implicitly
now follow this policy; use `ignore_urls` to skip intentional example URLs.

Checks reject credentials in URLs and HTTPS redirects to HTTP. Environment proxies and netrc
credentials are not used. HTTPS certificates and hostnames are verified.

```yaml
plugins:
  - htmlproofer:
      validate_external_urls: False
```

### `allow_private_hosts`

An empty list by default. Trusted site operators can permit intentional internal checks by listing
exact hostnames or IP literals (without schemes, paths, ports, or wildcards). Each listed host can
connect to its resolved addresses, including private ones. Redirects to other hosts still require
their own permission. Only allow hosts whose access is appropriate for every documentation
contributor; a hostname permission also trusts that host's DNS administrator.

```yaml
plugins:
  - htmlproofer:
      allow_private_hosts: ['docs.internal.example']
```

### `ca_bundle`

Defaults to the Requests trusted CA bundle. Set an explicit PEM CA bundle path when checking a
site using private PKI. The bundle must include all CAs needed by the site's external checks.
Certificate and hostname verification remain enabled. Environment CA-bundle variables are not used.

```yaml
plugins:
  - htmlproofer:
      ca_bundle: /path/to/trusted-ca-bundle.pem
```

### `validate_rendered_template`

Validates the entire rendered template for each page - including the navigation, header, footer, etc.
This defaults to off because it is much slower and often redundant to repeat for every single page.

```yaml
plugins:
  - htmlproofer:
      validate_rendered_template: True
```

### `strict_anchors`

Off by default, when an anchor is accepted if either the rendered page contains it or its Markdown
source provides it. Turn it on to accept only the anchors a page renders, reporting a link to one which
doesn't exist, such as `#heading` where `attr_list` replaced it in `## Heading {#custom-id}`, or an
anchor appearing only inside a fenced code block.

It decides links written with a path to a Markdown page, `page.md#anchor`, including one back into the
page holding it. An anchor on a page which isn't Markdown isn't checked at all. A bare `#anchor` reads
the same either way, resolved against every id the page renders, the theme's included.

For a `page.md` whose heading is written as `## Renamed Heading { #custom-id }`:

* `page.md#custom-id` is accepted whether the option is on or off.
* `page.md#renamed-heading` is accepted by default, and reported with the option on.
* `#renamed-heading`, written on `page.md` itself, is reported either way, the page rendering no such id.

```yaml
plugins:
  - htmlproofer:
      strict_anchors: True
```

### `skip_downloads`

Defaults to `True`: perform a streaming GET, inspect status/redirect headers, then close the response
without downloading its body. GET-only servers remain supported. Redirect bodies are always skipped.
Set it to `False` to consume the final response body within the byte and time limits below. Requests
ask for identity encoding; encoded bodies are rejected when downloads are enabled to avoid
unbounded decompression work.

```yaml
plugins:
  - htmlproofer:
      skip_downloads: True
```

### `max_download_bytes`

Limits body consumption when `skip_downloads` is `False`. Defaults to 10485760 bytes (10 MiB),
must be positive, and applies to observed bytes even if Content-Length is absent or incorrect.
An excessive declared or observed size is reported as `-1`.

### `request_timeout`

A positive, finite total time budget in seconds for one external check attempt, including all its
redirects and optional body reads. Defaults to `30.0`. A socket deadline interrupts continuously
progressing reads; individual connection/read inactivity is also limited to ten seconds. Expiration
is reported as `504`. DNS resolution uses the platform resolver; if it stalls, its operating-system
timeout still applies, and no connection is made after the check's deadline has expired.
Each configured retry gets a fresh budget; backoff delays are additional.

```yaml
plugins:
  - htmlproofer:
      skip_downloads: False
      max_download_bytes: 1048576
      request_timeout: 15.0
```

### `retry_max_times`

Sets the maximum number of HTTP request retries when checking an external URL. Defaults to 0 (no retries).
Retries back off exponentially, starting at 2 seconds. Local links and anchors are not retried.

```yaml
plugins:
  - htmlproofer:
      retry_max_times: 3
```

### `max_workers`

Optionally set the maximum number of worker threads used to validate URLs concurrently.
By default, this is not set and the [default of Python's `ThreadPoolExecutor`](https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.ThreadPoolExecutor) is used.

```yaml
plugins:
  - htmlproofer:
      max_workers: 16
```

### `user_agent`

The `User-Agent` to send when requesting an external URL. A browser's by default, because sites and the
CDNs in front of them increasingly answer anything else with a `403`, which is reported as a broken link
although the page opens in a browser.

Set it to identify your build instead:

```yaml
plugins:
  - htmlproofer:
      user_agent: 'Bot (https://example.com/)'
```

## Compatibility with `attr_list` extension

If you need to manually specify anchors make use of the `attr_list` [extension](https://python-markdown.github.io/extensions/attr_list) in the markdown.
This can be useful for multilingual documentation to keep anchors as language neutral permalinks in all languages.

* A sample for a heading `# Grüße {#greetings}` (the slugified generated anchor `grue` is overwritten with `greetings`).
* This also works for images `this is a nice image ![](foo-bar.png){#nice-image}`
* And generally for paragraphs:
```markdown
Listing: This is noteworthy.
{#paragraphanchor}
```

## Improving

More information about plugins in the [MkDocs documentation](http://www.mkdocs.org/user-guide/plugins/)

## Acknowledgement

This work is based on the [mkdocs-markdownextradata-plugin](https://github.com/rosscdh/mkdocs-markdownextradata-plugin) project and the [Finding and Fixing Website Link Rot with Python, BeautifulSoup and Requests](https://www.twilio.com/en-us/blog/find-fix-website-link-rot-python-beautifulsoup-requests-html) article.
