# GitHub Actions build debug — v26.09.02.03

The first `v26.09.01.04` GitHub run produced a valid Intel artifact, while the Apple Silicon job never executed a workflow step. GitHub reported:

- `The job was not acquired by Runner of type hosted even after multiple attempts`
- `Internal server error` with a GitHub correlation ID

That failure occurs in GitHub's hosted-runner scheduler before repository code starts, so there is no application stack trace or shell log to debug from that job.

The CI hardening introduced after that failure and retained in this revision is:

1. moves Apple Silicon from `macos-26` to the mature native ARM64 `macos-15` standard pool;
2. moves Intel from `macos-26-intel` to `macos-15-intel` for a consistent runner generation;
3. updates `actions/checkout` to v6 and `actions/upload-artifact` to v6 so the workflow uses Node.js 24-native actions and removes the `upload-artifact@v4` Node.js 20 warning;
4. keeps the explicit `platform.machine()` and `lipo` architecture checks, so a mis-routed runner cannot silently produce the wrong build;
5. adds a 45-minute execution timeout after a runner is acquired.

Later NMS revisions also improve Discovery responsiveness/hostname resolution and simplify Setup, but those application changes are independent of the hosted-runner acquisition failure described above.

If GitHub again reports a hosted-runner acquisition error and there are no step logs, re-run the failed job. That class of failure is upstream of the repository and cannot be retried from inside a job because no runner has started executing the workflow yet.
