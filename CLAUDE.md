# KonstanzGo Live backend — working rules

- At most 1 request/s to EFA-BW (burst 2, ≤ 60 a minute), one instance only. Never raise the caps in `Config.swift`.
- Tests and load tests never call EFA-BW: fixtures in `Tests/LiveCoreTests/Fixtures`, `loadtest/fake_efa.py`.
- LiveCore never imports Vapor. Swift 6, strict concurrency.
- No personal data, no IP logging, configuration from environment variables only, no secrets in the repo.
- Trip keys: `<yyyyMMdd>|<line>|<first stop gid>|<first departure minute>`. Never key on EFA's numeric ids.
- `KGLiveAPI/` is the original of the app's `Packages/KGLiveAPI`: add fields only; check with `scripts/check-api.sh`.
- `data/timetable.db` must be the same feed as the app's (`scripts/update-timetable.sh`).
- Run only the tests for what changed: `swift test --filter <Suite>`.
