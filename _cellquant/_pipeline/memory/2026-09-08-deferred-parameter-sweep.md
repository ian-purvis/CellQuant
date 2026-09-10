# Deferred request: parameter optimization against coexpression accuracy

User instruction, 2026-09-08:

After the user has tested CellQuant and is satisfied that the pipeline works
correctly, run an image through many parameter combinations, examine the final
coexpression results, and record the settings producing the most accurate
coexpression results.

Status: DEFERRED. Do not start now. The user must explicitly confirm the pipeline
works correctly and is satisfactory before this work begins. Passing software
tests, elapsed time, or completing the current implementation does not satisfy
this prerequisite. Do not schedule an automatic start.

Current authorized work is reliability fixes and guided threshold calibration
(steps 1 and 2). Small synthetic regression tests and user-directed review of an
individual threshold are part of that work; they are not the deferred search.

When the user releases the prerequisite, agree on expert-reviewed reference
coexpression calls/counts and an accuracy metric before optimizing. More positive
cells or a higher coexpression percentage alone does not mean greater accuracy.
Record image identity, masks, complete parameter settings, metric results,
runtime, software/model versions, and all unsuccessful combinations as well as
the selected settings. Keep tuning and final validation data distinguishable.
