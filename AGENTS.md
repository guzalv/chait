# Agent Instructions

## Critical

- **Understand before acting.** Read the code and its tests before changing anything. Trace the call chain far enough to know what breaks.
- Run `make test` before creating commits. Fix failures caused by your changes. Pre-existing failures: commit with `--no-verify`.
- Never push directly to main. Always create a PR (draft first).
- Branch off latest main before starting new work (fetch/pull first), unless the topic depends on an unmerged branch.
- Don't guess. Research the code. If you only have a hypothesis, say so and explain how to prove it.
- Review vs fix: when asked to review, only report findings. Do not change code unless explicitly asked.
- When reviewing PRs, be thorough, direct, picky. Group findings by severity. Do not soften feedback to be polite.
- Never post comments on GitHub without explicit user approval. Draft first, wait for go-ahead. Comments must state they were written by an AI.

## Tone

- Be direct, pragmatic, critical. No praise, no filler, no hedging.
- Disagree when the evidence says so. Correctness over agreement.
- Minimize output. Say what's needed, nothing more.

## Development

- One logical change per commit. Run tests after each change.
- Commit messages explain *why*, not *what*.
- Create PRs as drafts (`gh pr create --draft`). Mark ready only when asked.
- Don't force-push without confirming.
- Every PR that changes the product must include a worklog entry in the same PR (see table below).

## Task-specific guidance

Check this table before doing one of these tasks — don't re-derive the format by digging through git history or old PRs.

| Task | File |
|---|---|
| Writing a PR description | `agents/pr-template.md` |
| Writing a worklog entry | `agents/worklog-template.md` |
