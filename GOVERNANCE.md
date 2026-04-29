# Project governance

BioModel Monitor is an open project. This document explains how decisions
get made and how to take on more responsibility over time.

## Roles

| Role | What they can do | How you become one |
| ---- | ---------------- | ------------------ |
| **Contributor** | Open issues and PRs. | Anyone. |
| **Reviewer** | Review PRs in their domain area, request changes, approve. | Sustained, high-quality contributions in that area. Invited by a maintainer. |
| **Maintainer** | Merge PRs, cut releases, vote on RFCs, manage the GitHub org. | Invited by existing maintainers, by majority vote. |

There is no minimum hours-per-week requirement. The expectation for
maintainers is "respond to issues and PRs in your area within a week or
flag that you're out". Stepping back is fine and reversible.

## Decisions

- **Day-to-day code changes.** Two reviewer approvals (or one maintainer
  approval) are sufficient. CI must be green.
- **API changes / new public modules.** Open a short RFC issue first.
  Maintainer consensus (lazy consensus over 7 days) is required.
- **Breaking changes.** Require a changelog entry under "Breaking" and at
  least one full minor-version deprecation cycle when feasible.
- **Release cuts.** Any maintainer can cut a release once the changelog is
  updated and CI is green on `main`.

## Code of Conduct

By participating in this project you agree to follow the
[Contributor Covenant v2.1](https://www.contributor-covenant.org/version/2/1/code_of_conduct/).
Report unacceptable behaviour by opening a private issue with a maintainer.

## Trademark and licence

The BioModel Monitor source is open under the project's `LICENSE`. The name
"BioModel Monitor" and the project logo aren't trademarks; please don't
use them in a way that suggests the project endorses your fork or product
without checking with the maintainers first.
