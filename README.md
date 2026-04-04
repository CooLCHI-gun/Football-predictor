# HKJC 足球讓球研究與執行框架（MVP）

本專案是以 Windows + PowerShell 為主的 Python 研究流程，目標是把 Phase 3 到 Phase 6 串成可重跑、可驗證、可控風險的路線。

核心定位
- Phase 3 到 Phase 5：離線特徵、回測、優化（研究與驗證）。
- Phase 6：HKJC live 監測與 Telegram 研究提示（先 dry-run，再 live）。

重要規則
- 讓球結算只用全場 90 分鐘加傷停補時。
- 不可把加時賽與十二碼納入標籤、結算、回測或警示。

---

## 1. 安裝與環境（Windows / PowerShell）

Python 版本
- 建議固定使用 Python 3.11.9。

建立並啟用虛擬環境

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python --version
python -m pip install --upgrade pip
pip install -r requirements.txt
```

初始化資料庫

```powershell
.\.venv\Scripts\Activate.ps1
python -m src.main init-db
```

複製環境變數範本

```powershell
Copy-Item .env.example .env
```

---

## 2. 資料與特徵（Phase 3）

建議資料路徑
- NON_HKJC 全量特徵：data\processed\features_phase3_full.csv
- HKJC 回測特徵（若已建立）：data\processed\features_phase3_hkjc.csv

HKJC 歷史資料收集（重用 Phase 6 已驗證 GraphQL replay 方法）

```powershell
.\.venv\Scripts\Activate.ps1

python -m src.main collect-hkjc-history `
  --start-date 2026-03-01 `
  --end-date 2026-03-31 `
  --raw-output-dir artifacts\hkjc_history\raw `
  --output-path data\raw\hkjc\historical_matches_hkjc.csv `
  --feature-output-path data\processed\features_phase3_hkjc.csv `
  --build-features `
  --force
```

輸出重點
- 原始資料：artifacts\hkjc_history\raw\match_results.csv、market_hdc_rows.csv、result_details.csv
- 正規化資料：data\raw\hkjc\historical_matches_hkjc.csv
- Phase 3 特徵：data\processed\features_phase3_hkjc.csv

限制說明
- 此流程重用 Phase 6 的 HKJC provider / GraphQL request shape，不使用新 scraping stack。
- 若某些日期區間未提供完整盤口時間序，會採目前可得的 close-only 或近似開收盤基線，並保留後續擴充點。

建立 full 特徵檔（NON_HKJC）

```powershell
.\.venv\Scripts\Activate.ps1

python -m src.main build-features-full `
  --input-path data\raw\real\historical_matches_real_non_hkjc.csv `
  --output-path data\processed\features_phase3_full.csv `
  --force
```

若你已完成資料下載，也可直接使用既有 full 特徵檔進入回測與優化。

可配置特徵欄位清單（資料來源 + 缺失值策略）
- 預設配置檔：`config\feature_fields.json`
- 可透過 `FEATURE_FIELD_CONFIG_PATH` 或 CLI `--feature-field-config-path` 覆寫
- 可配置項目：欄位啟用順序（`active_fields`）、缺失值策略（`missing_strategy`）、欄位來源（`source`）
- 內建啟動前驗證：若 `active_fields` 欄位拼寫錯誤或 `missing_strategy` 非法，`build-features*` 會立即失敗（fail-fast）
- 已實作並可配置欄位：`fixture_density_7d_home`、`fixture_density_14d_away`、`line_drift_60m`

```powershell
python -m src.main build-features-full `
  --input-path data\raw\real\historical_matches_real_non_hkjc.csv `
  --output-path data\processed\features_phase3_full.csv `
  --feature-field-config-path config\feature_fields.json `
  --force
```

---

## 3. 回測與優化（Phase 4-5）

### 3.1 Canonical backtest（NON_HKJC full features）

```powershell
.\.venv\Scripts\Activate.ps1

python -m src.main backtest `
  --input-csv-path data\processed\features_phase3_full.csv `
  --output-dir artifacts\backtest `
  --run-id quick_check `
  --force
```

輸出位置
- artifacts\backtest\quick_check\predictions.csv
- artifacts\backtest\quick_check\trade_log.csv
- artifacts\backtest\quick_check\summary.csv

### 3.2 Canonical optimizer（coverage-balanced 窄網格 + cache）

```powershell
.\.venv\Scripts\Activate.ps1

python -m src.main optimize `
  --input-csv-path data\processed\features_phase3_full.csv `
  --output-dir artifacts\optimizer `
  --run-id opt_coverage_balance `
  --edge-grid 0.01,0.015,0.02 `
  --confidence-grid 0.0,0.05,0.10 `
  --max-alerts-grid 1,2 `
  --policy-grid fractional_kelly `
  --kelly-grid 0.15 `
  --max-stake-grid 0.01 `
  --daily-exposure-grid 0.03 `
  --max-runs 60 `
  --use-prediction-cache `
  --force
```

輸出位置
- artifacts\optimizer\opt_coverage_balance\params_results.csv
- artifacts\optimizer\opt_coverage_balance\best_params.json

多目標 optimizer 模板（ROI + Win rate + placed bets + drawdown）

```powershell
.\.venv\Scripts\Activate.ps1

$env:OPTIMIZER_LAMBDA_DRAWDOWN = "0.55"
$env:OPTIMIZER_LAMBDA_ROR = "0.60"
$env:OPTIMIZER_MU_CLV = "0.20"
$env:OPTIMIZER_MU_WIN_RATE = "0.35"
$env:OPTIMIZER_MU_PLACED_BETS = "0.25"
$env:OPTIMIZER_TARGET_PLACED_BETS = "120"
$env:OPTIMIZER_LAMBDA_LOW_BETS = "0.10"
$env:OPTIMIZER_MIN_BETS_TARGET = "60"

python -m src.main optimize `
  --input-csv-path data\processed\features_phase3_full.csv `
  --output-dir artifacts\optimizer `
  --run-id opt_multi_objective `
  --edge-grid 0.01,0.015,0.02 `
  --confidence-grid 0.05,0.10,0.15 `
  --max-alerts-grid 1,2,3 `
  --policy-grid flat,fractional_kelly `
  --kelly-grid 0.10,0.15,0.20 `
  --max-stake-grid 0.008,0.01 `
  --daily-exposure-grid 0.02,0.03 `
  --max-runs 120 `
  --use-prediction-cache `
  --force
```

### 3.3 NON_HKJC baseline 解讀（目前共識）

coverage-balanced 參數（來自 NON_HKJC 歷史）
- min_edge_threshold 約 0.01
- min_confidence_threshold 約 0.05
- max_alerts = 1（映射 max_concurrent_bets）
- policy = fractional_kelly
- fractional_kelly_factor 約 0.15
- max_stake_pct 約 0.01
- daily_max_exposure_pct 約 0.03

目前觀察（研究用途）
- 約 174 bets
- ROI 約 3%
- max drawdown 約 10%
- CLV 欄位在這批 NON_HKJC 資料接近 0，現階段不具判讀力

重要聲明
- 此 preset 屬研究/觀察，不是已證實穩定優勢策略。
- 任何提高注碼或放寬門檻前，應先做 HKJC 專用回測與再次驗證。

### 3.4 HKJC-only 重新驗證（建議）

```powershell
.\.venv\Scripts\Activate.ps1

$env:BACKTEST_DATASET_SCOPE = "HKJC"
$env:MIN_EDGE_THRESHOLD = "0.01"
$env:MIN_CONFIDENCE_THRESHOLD = "0.05"
$env:MAX_CONCURRENT_BETS = "1"
$env:FRACTIONAL_KELLY_FACTOR = "0.15"
$env:BANKROLL_MAX_STAKE_PCT = "0.01"
$env:BANKROLL_DAILY_MAX_EXPOSURE_PCT = "0.03"

python -m src.main backtest `
  --input-csv-path data\processed\features_phase3_hkjc.csv `
  --output-dir artifacts\backtest `
  --run-id hkjc_coverage_balanced `
  --force
```

HKJC summary 分析（CLV + ROI）

```powershell
.\.venv\Scripts\Activate.ps1

python -m src.main analyze-hkjc `
  --summary-csv-path artifacts\backtest\hkjc_coverage_balanced\summary.csv
```

---

## 4. Live Monitoring（Phase 6, HKJC）

### 4.1 研究 dry-run preset（建議起步）

- provider: hkjc
- model path: artifacts\model_bundle.pkl
- edge-threshold = 0.02
- confidence-threshold = 0.10
- max-alerts = 3
- TELEGRAM_DRY_RUN = "true"

```powershell
.\.venv\Scripts\Activate.ps1
$env:TELEGRAM_DRY_RUN = "true"

python -m src.main live-run-once `
  --provider hkjc `
  --model-path artifacts\model_bundle.pkl `
  --dry-run `
  --edge-threshold 0.02 `
  --confidence-threshold 0.10 `
  --max-alerts 3 `
  --force
```

### 4.2 production-safe preset（小量 live）

- edge-threshold 約 0.02
- confidence-threshold 約 0.10 到 0.12
- max-alerts = 1
- bankroll 建議維持：fractional Kelly 0.15、max stake 1%、daily exposure 3%

```powershell
.\.venv\Scripts\Activate.ps1
$env:TELEGRAM_DRY_RUN   = "false"
$env:TELEGRAM_BOT_TOKEN = "<your_bot_token>"
$env:TELEGRAM_CHAT_ID   = "<your_chat_id>"

python -m src.main live-run-once `
  --provider hkjc `
  --model-path artifacts\model_bundle.pkl `
  --live `
  --edge-threshold 0.02 `
  --confidence-threshold 0.10 `
  --max-alerts 1 `
  --force
```

輸出檢查重點
- artifacts\live\live_snapshot.csv
- artifacts\live\live_candidates.csv
- artifacts\live\live_alert_preview.txt
- artifacts\live\live_alert_log.csv

重要提醒
- Phase 6 alert 是研究信號，不是投注指示。

### 4.3 validate-results（HKJC 結果對齊）

```powershell
.\.venv\Scripts\Activate.ps1

python -m src.main validate-results `
  --start-date 2026-04-01 `
  --end-date 2026-04-03 `
  --output-path artifacts\live\results_validation.csv `
  --force
```

---

## 5. Security 與 Secrets

必須遵守
- Telegram token / chat id 只可來自環境變數、.env（本機）或 GitHub Secrets（雲端）。
- 不可硬編碼在程式碼、README、user.md 或 artifacts。
- .env.example 只保留 placeholder（例如 <your_bot_token>、<your_chat_id>）。
- 公開倉庫不可提交 .env、.har、含 token/header/cookies 的 debug 檔。

PowerShell 與 GitHub Actions 語法分流
- PowerShell：使用 $env:NAME = "value"
- GitHub Actions：使用 secrets 注入（只在 YAML）

for GitHub Actions only

```yaml
env:
  TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
  TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
  TELEGRAM_DRY_RUN: ${{ secrets.TELEGRAM_DRY_RUN }}
```

## 5.1 GitHub Actions（取代 Railway）

本專案已提供兩個工作流程：
- `.github/workflows/ci.yml`：push / pull request 觸發，執行核心 smoke 與回測測試。
- `.github/workflows/scheduled-hkjc.yml`：排程執行 backtest / optimize / live-run-once（dry-run）。

建議做法
- 把 daily 與 live 任務改由 GitHub Actions 排程，不再依賴 Railway 常駐程序。
- 先用 `workflow_dispatch` 手動跑通，再啟用 schedule。

需要設定的 Secrets / Variables
- Secrets：`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`、`TELEGRAM_DRY_RUN`
- Variables（選填）：`LIVE_MODE`

排程說明（UTC）
- `30 17 * * *`：01:30 HKT backtest
- `30 19 * * *`：03:30 HKT optimize
- `*/15 * * * *`：每 15 分鐘一次 live one-shot（dry-run）

備註
- GitHub Actions 最短排程粒度是 5 分鐘，且可能有排程延遲，不保證秒級準時。
- 若要真實送出 Telegram，請把 `TELEGRAM_DRY_RUN` 設為 `false` 並確認金鑰配置。

---

## 6. 常用命令快速表

```powershell
.\.venv\Scripts\Activate.ps1
python -m src.main --help
python -m src.main build-features --help
python -m src.main build-features-full --help
python -m src.main download-real-data --help
python -m src.main train --help
python -m src.main predict --help
python -m src.main predict-full --help
python -m src.main backtest --help
python -m src.main optimize --help
python -m src.main daily-maintenance --help
python -m src.main analyze-hkjc --help
python -m src.main live-run-once --help
python -m src.main live-loop --help
python -m src.main validate-results --help
python -m src.main alert --help
```

---

## 6.1 Railway 單一 command 每日雙時段排程

當 Railway 只能設定一條啟動 command 時，可用 `daily-maintenance` 在同一個程序內分開時間執行 backtest 與 optimizer。

```powershell
python -m src.main daily-maintenance `
  --timezone-name Asia/Hong_Kong `
  --backtest-time 01:30 `
  --optimize-time 03:30 `
  --backtest-input-path data\processed\features_phase3_full.csv `
  --optimize-input-path data\processed\features_phase3_full.csv `
  --backtest-output-dir artifacts\backtest `
  --optimize-output-dir artifacts\optimizer `
  --use-date-run-id `
  --use-prediction-cache `
  --max-runs 120 `
  --force
```

備註
- `--backtest-time` 與 `--optimize-time` 使用 `HH:MM`（24 小時制）。
- 預設時區為 `Asia/Hong_Kong`。
- 預設會自動加上日期 run-id（例如 `daily_backtest_20260404`）。
- `--repeat-daily` 可令同一程序每日持續循環。
- 本機驗證可加 `--skip-wait`，立即執行兩個流程而不等待時間。

快速驗證（立即執行，不等時間）

```powershell
python -m src.main daily-maintenance `
  --timezone-name Asia/Hong_Kong `
  --backtest-time 01:30 `
  --optimize-time 03:30 `
  --backtest-input-path data\processed\features_phase3_full.csv `
  --optimize-input-path data\processed\features_phase3_full.csv `
  --backtest-output-dir artifacts\backtest `
  --optimize-output-dir artifacts\optimizer `
  --use-date-run-id `
  --use-prediction-cache `
  --max-runs 30 `
  --skip-wait `
  --force
```

---

## 6.2 每日推薦與分析 command 清單

### A. 每日分析（Backtest + Optimizer + 分析報告）

1) 每日雙時段分析（Railway 單 command）

```powershell
python -m src.main daily-maintenance `
  --timezone-name Asia/Hong_Kong `
  --backtest-time 01:30 `
  --optimize-time 03:30 `
  --backtest-input-path data\processed\features_phase3_full.csv `
  --optimize-input-path data\processed\features_phase3_full.csv `
  --backtest-output-dir artifacts\backtest `
  --optimize-output-dir artifacts\optimizer `
  --use-date-run-id `
  --use-prediction-cache `
  --max-runs 120 `
  --force
```

2) 針對某日 backtest summary 產生分析建議

```powershell
python -m src.main analyze-hkjc `
  --summary-csv-path artifacts\backtest\daily_backtest_20260404\summary.csv
```

### B. 每日推薦（Prediction + Alert）

1) 產生全量預測

```powershell
python -m src.main predict-full `
  --input-path data\processed\features_phase3_full.csv `
  --model-path artifacts\model_bundle.pkl `
  --output-path artifacts\predictions_full.csv `
  --force
```

2) 輸出每日推薦（Telegram dry-run / live）

```powershell
# dry-run（建議先用）
$env:TELEGRAM_DRY_RUN = "true"
python -m src.main alert `
  --predictions-path artifacts\predictions_full.csv `
  --edge-threshold 0.02 `
  --confidence-threshold 0.56 `
  --max-alerts 3
```

```powershell
# live 發送（確認 token/chat id 後）
$env:TELEGRAM_DRY_RUN = "false"
python -m src.main alert `
  --predictions-path artifacts\predictions_full.csv `
  --edge-threshold 0.02 `
  --confidence-threshold 0.56 `
  --max-alerts 3
```

### C. 常見錯誤（`daily-maintenance` 退出碼 1）

- 只輸入 `python -m src.main daily-maintenance` 但已有舊檔案，可能被覆蓋保護擋住：加 `--force`。
- 輸入資料不存在：確認 `data\\processed\\features_phase3_full.csv` 存在。
- 時間格式錯誤：`--backtest-time` / `--optimize-time` 必須是 `HH:MM`。
- 想即刻測試流程：加 `--skip-wait`。

### D. Railway 專用最短一行版（每日分析 + 每5分鐘推薦）

```powershell
python -m src.main railway-job-once
```

建議 Railway 排程
- 每 5 分鐘觸發一次同一條 command：`python -m src.main railway-job-once`
- command 每次都會執行一次 `live-run-once` 後退出
- `backtest` / `optimize` 只會在到達指定時間後每日各執行一次（用 state file 去重）
- 可選開啟 `data-update`（`download-real-data` command），每日最多一次
- 可選開啟 `feature-rebuild`（`build-features-full` command），每日最多一次
- 可選開啟 `retrain`（`train` command），同樣每日最多一次，再接續當次 `live-run-once`
- 可選開啟 `switch gate`（混合轉 HKJC-only）並輸出每日審計 `artifacts/switch_decision.json`
- 可選開啟 `switch Telegram report`，每日推送 PASS/FAIL 與原因
- 可選開啟 `live auto-tune`，從當日 optimizer best params 自動調整 live 門檻

常用覆寫參數（可選）

```powershell
python -m src.main railway-job-once `
  --data-update-enabled `
  --data-update-time 00:15 `
  --feature-rebuild-enabled `
  --feature-rebuild-time 00:30 `
  --retrain-enabled `
  --retrain-time 00:45 `
  --switch-enabled `
  --switch-auto-apply `
  --switch-required-consecutive-passes 2 `
  --switch-telegram-report-enabled `
  --live-auto-tune-enabled `
  --backtest-time 01:30 `
  --optimize-time 03:30 `
  --live-mode dry `
  --force
```

如要真實發送 Telegram（非 dry-run）：
- 在 Railway 變數設 `LIVE_MODE=live`
- 並設定 `TELEGRAM_DRY_RUN=false`、`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`

可調整的 Railway 環境變數（選填）
- `DATA_UPDATE_ENABLED`（預設 `false`；`true` 會啟用每日資料更新）
- `DATA_UPDATE_TIME`（預設 `00:15`）
- `DATA_UPDATE_URLS`（預設沿用 settings 的 FOOTBALL_DATA_SOURCE_URLS）
- `DATA_UPDATE_RAW_DIR`（預設 `data/raw/real`）
- `DATA_UPDATE_NORMALIZED_OUTPUT_PATH`（預設 `data/raw/real/historical_matches_real_non_hkjc.csv`）
- `FEATURE_REBUILD_ENABLED`（預設 `false`；`true` 會啟用每日特徵重建）
- `FEATURE_REBUILD_TIME`（預設 `00:30`）
- `FEATURE_REBUILD_INPUT_PATH`（預設沿用 `DATA_UPDATE_NORMALIZED_OUTPUT_PATH`）
- `FEATURE_REBUILD_OUTPUT_PATH`（預設沿用 `FEATURE_PATH`）
- `RETRAIN_ENABLED`（預設 `false`；`true` 會啟用每日重訓）
- `RETRAIN_TIME`（預設 `00:45`）
- `RETRAIN_INPUT_PATH`（預設沿用 `FEATURE_REBUILD_OUTPUT_PATH`，未設定時再沿用 `FEATURE_PATH`）
- `RETRAIN_MODEL_OUTPUT_PATH`（預設沿用 `LIVE_MODEL_PATH`）
- `RETRAIN_REPORT_OUTPUT_PATH`（預設 `artifacts/train_report.json`）
- `BACKTEST_TIME`（預設 `01:30`）
- `OPTIMIZE_TIME`（預設 `03:30`）
- `TIMEZONE_NAME`（預設 `Asia/Hong_Kong`）
- `FEATURE_PATH`（預設 `data/processed/features_phase3_full.csv`）
- `BACKTEST_OUTPUT_DIR`（預設 `artifacts/backtest`）
- `OPTIMIZER_OUTPUT_DIR`（預設 `artifacts/optimizer`）
- `OPTIMIZER_MAX_RUNS`（預設 `120`）
- `LIVE_PROVIDER`（預設 `hkjc`）
- `LIVE_MODEL_PATH`（預設 `artifacts/model_bundle.pkl`）
- `LIVE_EDGE_THRESHOLD`（預設 `0.02`）
- `LIVE_CONFIDENCE_THRESHOLD`（預設 `0.56`）
- `LIVE_MAX_ALERTS`（預設 `3`）
- `LIVE_OUTPUT_DIR`（預設 `artifacts/live`）
- `RAILWAY_STATE_PATH`（預設 `artifacts/railway_job_state.json`）
- `SWITCH_ENABLED`（預設 `false`；啟用混合轉 HKJC-only 門檻判定）
- `SWITCH_AUTO_APPLY`（預設 `false`；門檻連續通過後自動切至 HKJC feature）
- `SWITCH_HKJC_SUMMARY_PATH`（預設 `artifacts/backtest/hkjc_coverage_balanced/summary.csv`）
- `SWITCH_MIXED_SUMMARY_PATH`（預設空；可填 mixed baseline summary 供差值比較）
- `SWITCH_HKJC_FEATURE_PATH`（預設 `data/processed/features_phase3_hkjc.csv`）
- `SWITCH_HKJC_RETRAIN_INPUT_PATH`（預設空；未設時沿用 `SWITCH_HKJC_FEATURE_PATH`）
- `SWITCH_MIN_MATCHES`（預設 `500`）
- `SWITCH_MIN_TOTAL_BETS`（預設 `120`）
- `SWITCH_MIN_ROI`（預設 `0.015`）
- `SWITCH_MIN_WIN_RATE`（預設 `0.515`）
- `SWITCH_MAX_DD`（預設 `0.12`）
- `SWITCH_MAX_ROI_GAP_TO_MIXED`（預設 `0.005`）
- `SWITCH_MAX_DD_GAP_TO_MIXED`（預設 `0.02`）
- `SWITCH_MAX_BET_DROP_RATIO`（預設 `0.25`）
- `SWITCH_REQUIRE_HKJC_SOURCE`（預設 `true`）
- `SWITCH_REQUIRED_CONSECUTIVE_PASSES`（預設 `2`）
- `SWITCH_DECISION_OUTPUT_PATH`（預設 `artifacts/switch_decision.json`）
- `SWITCH_TELEGRAM_REPORT_ENABLED`（預設 `false`；推送 switch gate 分析到 Telegram）
- `LIVE_AUTO_TUNE_ENABLED`（預設 `false`；從 optimizer best params 自動調整 live 門檻）
- `LIVE_AUTO_TUNE_MIN_EDGE`（預設 `0.01`）
- `LIVE_AUTO_TUNE_MAX_EDGE`（預設 `0.03`）
- `LIVE_AUTO_TUNE_MIN_CONFIDENCE`（預設 `0.50`）
- `LIVE_AUTO_TUNE_MAX_CONFIDENCE`（預設 `0.60`）
- `LIVE_AUTO_TUNE_MIN_ALERTS`（預設 `1`）
- `LIVE_AUTO_TUNE_MAX_ALERTS`（預設 `3`）

每次 `train`（包括 `railway-job-once` 內的 retrain）完成後，會自動輸出 proxy 特徵監控檔：
- `artifacts/debug/proxy_feature_monitor.json`
- `artifacts/debug/proxy_feature_monitor.csv`

欄位解讀重點：
- `importance_value` / `importance_rank`：來自 feature importance，數值越高代表模型越常使用該 proxy 特徵。
- `missing_rate`：缺失比例（`1.0` 代表全部缺失）。
- `drift_abs`：前半段與後半段樣本均值的絕對差。
- `drift_ratio`：`drift_abs / abs(first_half_mean)`，用於看相對漂移強度。
- `drift_cohen_d`：以前後半段 pooled std 計算的效果量，絕對值越大代表分布漂移越明顯。

實務上可先用以下規則做保留/降權候選檢查：
- 低重要度 + 高缺失（例如 `importance_value` 接近 0 且 `missing_rate` 長期 > 0.9）。
- 低重要度 + 高漂移（例如 `importance_value` 接近 0 且 `abs(drift_cohen_d)` 持續偏高）。

switch 審計檔內容重點
- `passed`：今日門檻是否通過
- `reasons`：失敗原因清單
- `pass_streak`：連續通過日數
- `switch_mode`：`MIXED` 或 `HKJC_ONLY`
- `auto_apply_effective`：今日是否已實際套用 HKJC 路徑

PowerShell / venv 說明
- 啟動腳本會強制使用 `.venv\Scripts\python.exe`
- 啟動前會檢查 Python 版本必須為 `3.11`
- 或直接使用 `python -m src.main railway-job-once`，不需額外 PowerShell 包裝

---

## 7. 最短端到端流程（Phase 3 到 Phase 6）

```powershell
.\.venv\Scripts\Activate.ps1

python -m src.main backtest `
  --input-csv-path data\processed\features_phase3_full.csv `
  --output-dir artifacts\backtest `
  --run-id quick_check `
  --force

python -m src.main optimize `
  --input-csv-path data\processed\features_phase3_full.csv `
  --output-dir artifacts\optimizer `
  --run-id opt_quick `
  --max-runs 30 `
  --use-prediction-cache `
  --force

$env:TELEGRAM_DRY_RUN = "true"
python -m src.main live-run-once `
  --provider hkjc `
  --model-path artifacts\model_bundle.pkl `
  --dry-run `
  --edge-threshold 0.02 `
  --confidence-threshold 0.10 `
  --max-alerts 3 `
  --force
```
