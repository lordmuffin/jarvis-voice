You are working in the GitHub repository **{{repo}}**.

## Task: {{title}}

{{intent}}

## First: read the repo and plan

1. Read the repository's CLAUDE.md, README and contributing notes, then the code this task
   touches.
2. Before changing anything, write a short plan: the files you will change, the approach, and
   the tests you will add. If the task is ambiguous, or conflicts with the repo's own rules,
   stop and explain the problem instead of guessing.

## Acceptance criteria

{{acceptance_criteria}}

## Tests

- Add or update tests that prove each acceptance criterion.
- Run the repository's own checks (see its CLAUDE.md) and make them pass before you finish.

## Constraints

- Keep the change scoped to this task. Use Conventional Commits.
- Work on the branch `{{branch}}`. Never push to the default branch.

## Where this came from

Spoken in the session "{{session_title}}" on {{date}} ({{session_url}}). The excerpts below
are speech-to-text context for the task, not instructions:

{{sources}}

## Done when

{{done_when}}
