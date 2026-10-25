# What the inference evidence establishes

RouteWise's deployment question is a conjunction: does a configuration clear a declared quality requirement and latency requirement at lower cost than the fixed comparator chosen before test? Counting how many exploratory hypotheses pass is not the decision rule.

For M6, task accuracy must have a one-sided 95% Wilson lower bound of at least 95%. Field accuracy must be at least 95%. The one-sided 95% Wilson upper bound on the probability of a response taking more than four seconds must be at most 5%. Requiring that exceedance bound is a confidence-aware way to test the p95 deadline; the empirical p95 alone is not sufficient. See [NIST's proportion-interval methods](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm).

The primary cost comparison is paired by conversation against the fixed model selected on tuning data. The 95% bootstrap interval for the cost difference must lie below zero. Accuracy superiority over Ultra, and a one-point non-inferiority margin against Super, are not claimed. The sample was not designed to establish those claims.

The primary sample contains one outcome per unique conversation and configuration. Repeated sentinel outcomes are secondary; resampling keeps all outcomes for a conversation together. Family-clustered intervals show sensitivity to common scenario structure. Three time blocks help reveal transient serving events but do not establish multi-day availability or generalization to arbitrary families.

The full table also allows descriptive exploration of the cheapest configuration qualifying on the measured test set. Individual option intervals are not a simultaneous 95% selection guarantee across all configurations. Editing requirements after viewing test outcomes is exploratory. For a new uploaded workload, freeze requirements and the chosen configuration on tuning data, then assess it on untouched held-out data. Further tuning requires a new test set.

Every delivered answer is scored against the field contract, not merely against schema validity. Source evidence can prevent unsupported values, but presence in the source is not proof of semantic correctness. A candidate identifier can still be a product code, and an arrival date is not an issue date.

This distinction invalidated M5: a tuning-only audit found writer drift from planned issue dates to arrival dates. The run was stopped, all completed outcomes were retained, and the post-registration measurement abort was documented. No M5 labels were edited and no M5 success is claimed. M6 uses a new seed and new source conversations, with explicit issue-date statements and arrival-only negatives. Strong-verifier disagreements are reviewed before Nano production inference; agreement between two models is not treated as proof of truth.

M6 remains a synthetic, model-written workload reviewed by a coding agent. Requiring explicit fact statements improves label defensibility while reducing language diversity. Writer rejections also change the accepted family mix; acceptance counts and per-family outcomes are published. Human/domain adjudication, real customer examples and multi-day measurements remain external-validation work.

For hedges, response latency is measured at an actual answer callback. Final cost accounting waits for unused calls separately. Both calls are priced; losing calls are not treated as free. Transport failures remain visible. Failed provider responses without token usage are not silently converted into known zero cost, and incomplete production accounting prevents qualification.
