# Repair behavior qualification — initial contract

EvalShot can assess a *declared* defect regression and preservation controls against distinct, disposable before/after repository workspaces. This is **not** a new agent loop or generic repair benchmark runner: it consumes the existing `scripts.agent_economics.bounded_process.run_bounded` execution authority.

`benchmarks.repair_qualification.check_repair` requires the defect regression to fail before and pass after the repair, while every declared preservation control passes before and after. Timeouts, missing executables, truncated evidence and missing controls do not qualify as repaired behavior. The result contains bounded command evidence rather than a subjective patch-quality grade.

Use the evaluator's independent frozen oracle to choose the regression and preservation commands. Do not allow the evaluated agent or candidate patch to redefine the governing oracle. Execute only in disposable copies with a pre-established trusted command policy: this helper is not an isolation boundary for adversarial programs.

This checkpoint proves **declared observable behavior only**. It does not establish root-cause deletion, exhaustive preservation, discovery quality, or skill effectiveness. Those claims require separately reviewed frozen tasks and comparable model-backed experiments. The selected agent/runtime owns code edits, reasoning and any iterative workflow. AgentsCookbook remains the canonical source of repair methodology.
