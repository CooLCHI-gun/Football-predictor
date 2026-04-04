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
python -m src.main analyze-hkjc --help
python -m src.main live-run-once --help
python -m src.main live-loop --help
python -m src.main validate-results --help
python -m src.main alert --help
```

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
