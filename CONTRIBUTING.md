# Contributing

Contributions are welcome — bug fixes, board profiles, instrument drivers, docs.

benchhud is source-available, and I sell commercial licences for use above the
[PolyForm Small Business](LICENSE) limits. That only works if the rights over
every line are clean, so two rules apply to every contribution.

## Sign off every commit (DCO)

Each commit must carry a [Developer Certificate of Origin](https://developercertificate.org/)
sign-off. It's your statement that you wrote the change, or otherwise have the
right to submit it. Add it with `-s`:

```sh
git commit -s -m "fix(overlay): describe the change"
```

That appends `Signed-off-by: Your Name <you@example.com>`, matching your git
identity. A pull request with an unsigned commit won't be merged — fix it with
`git commit --amend -s` or `git rebase --signoff main` and force-push.

## Licensing of contributions

By contributing, you agree that your contribution is licensed under the
project's [PolyForm Small Business License 1.0.0](LICENSE), and that it may be
included in commercially licensed versions of benchhud.

## Before you start

Open an issue before anything large — a new subsystem, a dependency, a change
to the config format. A quick note up front saves a rewrite later. Small fixes
can go straight to a pull request.

Style is enforced by the tooling, not by this file. Before you push:

```sh
uv run ruff check
uv run pytest
```
