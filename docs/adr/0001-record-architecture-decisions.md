# 0001 — Record architecture decisions

* **Status:** Accepted (v0.9)
* **Date:** 2026-05-02

## Context

BioModel Monitor has shipped nine minor releases in roughly a year. Several
choices that look obvious in code (severity buckets, opt-in tenancy, SQLite
as the default store) have already been re-litigated multiple times in PR
reviews and on issues — usually because the rationale lives only in the
original PR description and is hard to find later.

## Decision

Adopt lightweight ADRs (MADR-lite) under `docs/adr/`. One file per
decision, numbered, immutable once accepted (subsequent decisions
*supersede* rather than rewrite). The index on `docs/adr/index.md` is the
authoritative table.

## Consequences

* Reviewers can link to an ADR instead of re-explaining a constraint.
* Newcomers reading the docs site get the *why*, not just the *what*.
* We pay a small cost: each significant architectural change must add or
  supersede an ADR. We accept that cost as the lower bound of useful design
  rigour.
