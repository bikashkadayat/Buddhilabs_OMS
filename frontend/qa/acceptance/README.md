# Draft recovery acceptance tests (Phase 111.18 / 111.19)

These drive the **real running stack** over the Chrome DevTools Protocol — real
login, real API, real IndexedDB. They are not unit tests and are not part of
`npm test`; the unit suites cover the engine's logic, and these prove the thing
the feature actually promises: that work survives losing the browser.

`draft-recovery.mjs` is the acceptance criterion from the brief. It types a
five-page memo, **SIGKILLs the browser** (no unload event, no flush — the
closest a test can get to a power cut), relaunches a new browser on the same
profile, and checks the content comes back.

`draft-scenarios.mjs` covers the rest of the matrix: crash recovery for Minute
and Circular, going offline and reconnecting, and the server copy surviving
independently of any browser.

## Running

Start the stack first (`./run-local.sh --no-sync`), then:

    node frontend/qa/acceptance/draft-recovery.mjs /tmp/shots
    node frontend/qa/acceptance/draft-scenarios.mjs /tmp/shots

Both exit non-zero if any check fails, so they can gate a release. Screenshots
are written to the directory given as the argument.

They assume the local demo login (`demo.maker@example.test`) seeded by
`run-local.sh`.
