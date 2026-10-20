You are working in the GitHub repository **{{repo}}**.

## Task: {{title}}

{{intent}}

## First: read the repo and plan

1. Read the repository's CLAUDE.md, README and contributing notes, then the code this task
   touches.
2. Before changing anything, write a short plan: the files you will change, the approach, and
   the tests you will add. If the task is ambiguous, choose the reading that best fits the
   repo's own rules, note the assumption in your plan and keep going. If it truly conflicts
   with those rules, do what you safely can and say exactly what you need decided.

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
