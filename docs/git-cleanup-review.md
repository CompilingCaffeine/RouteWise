# Git cleanup review · October 26, 2026

Reviewed the pending guided-demo implementation, rules-only diagnostic, source
records, documentation, screenshots and historical planning material. Preserved
the existing study archives and grouped the changes into local commits. No push,
public deployment, YouTube upload or Devpost submission was performed.

Validation: **231 tests passed**, `git diff --check` passed, and every source hash
in the pending rules-diagnostic manifest and deployment decision matched. A fresh
real Nebius request was recorded; its synthetic input and full trace are retained
in [video/live-trace.json](video/live-trace.json). This is a functionality check,
not a new benchmark.

Items requiring judgment or follow-through:

- **Public app mismatch:** a fresh browser check still showed the earlier
  Overview/Workload optimizer navigation on the public Streamlit deployment.
  Push/deploy the committed guided build and verify Start here and Live router
  before submission. The video explicitly labels its footage as the local app.
- **Video publication:** the completed recording uses computer-generated
  narration. Review voice/pronunciation and publish it publicly on YouTube; add
  its URL to Devpost. Neither publication nor final submission was requested.
- **Evidence limits:** independent human labels are still missing, and M7's
  latency gate failed. The script retains both limitations. Do not present the
  synthetic benchmark as independently validated customer performance.
- **Historical material:** `routewise-agent-handoff.md` predates the current
  studies and is now labelled historical. The competitive review is retained as
  a dated research note; its competitor descriptions were not re-audited during
  this cleanup and should not be treated as current verified claims.
- **Git scope:** commits are local on the existing `main` branch. No history was
  rewritten and no original evidence was removed. Recording intermediates and
  the local source-capture archive are intentionally ignored; the final MP4,
  captions, script, live trace and recording manifest are versioned.
