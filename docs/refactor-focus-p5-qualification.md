# Refactor Focus P5 Qualification

Phase 5 qualifies repository discovery semantics and portability.

The adversarial corpus proves:

- `auto` selects the Git worktree when available and lists tracked/untracked state once for both source and test roots;
- default Git discovery includes tracked plus non-ignored untracked Python files and excludes ignored files;
- tracked-only and ignored-inclusive policies are explicit and measurable;
- normalized repository-relative exclusions are segment-aware, including the historical `docs/_build/**` case;
- single-segment globs do not cross directory boundaries;
- Python-file symlinks are excluded, rejected, or admitted only when their target remains inside the repository;
- discovery roots outside the repository, nested repository-root mistakes, and explicit Git mode outside a worktree fail closed;
- `auto` uses a deterministic filesystem fallback when Git is unavailable and publishes the authority loss as a warning;
- discovery configuration is part of configuration identity, and changing whether untracked source is admitted changes analyzed repository identity.

Promotion requires P1, P2, P3, P4, and P5 qualification to pass together.
