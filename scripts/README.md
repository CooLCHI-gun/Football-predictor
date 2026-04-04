# Scripts

PowerShell helper for quick local workflow:

```powershell
./scripts/dev.ps1 -Task test
./scripts/dev.ps1 -Task backtest
./scripts/dev.ps1 -Task optimize-small
./scripts/dev.ps1 -Task alert-dryrun
./scripts/phase6_acceptance.ps1
```

Phase 6 acceptance one-click script:

```powershell
./scripts/phase6_acceptance.ps1
./scripts/phase6_acceptance.ps1 -RunId 20260403_pm1 -LoopMaxCycles 2
```

Keep core production logic under src/.
