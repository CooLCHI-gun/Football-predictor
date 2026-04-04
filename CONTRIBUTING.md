# Contributing

本文件提供本專案在本機 VS Code 的最短開發流程（以 Windows PowerShell 為主）。

## 1. 環境建立

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python --version
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

備註
- 開發目標版本為 Python 3.11.x。
- 機密資訊只放在 `.env`（例如 Telegram token/chat id）。

## 2. 快速回歸（Phase 4.5 / 5）

```powershell
.\.venv\Scripts\Activate.ps1
pytest -q

python -m src.main backtest --input-path data\processed\features_phase3_full.csv --force
python -m src.main optimize --input-path data\processed\features_phase3_full.csv --edge-grid "0.03" --confidence-grid "0.55" --policy-grid "fractional_kelly,vol_target" --kelly-grid "0.25" --max-stake-grid "0.02" --daily-exposure-grid "0.03" --force
python -m src.main alert --predictions-path artifacts\predictions_full.csv --edge-threshold 0.02 --confidence-threshold 0.55 --max-alerts 3
```

## 3. 常見開發命令

```powershell
python -m src.main --help
python -m src.main train --help
python -m src.main predict --help
python -m src.main predict-full --help
python -m src.main backtest --help
python -m src.main optimize --help
python -m src.main alert --help
```

## 4. 擴充入口

新增特徵
- 主要檔案：`src/features/pipeline.py`
- 原則：只使用 kickoff 前可得資訊，避免 look-ahead。

新增模型
- 主要檔案：`src/models/baselines.py`
- 命令入口：`src/models/pipeline.py`

新增資金規則
- 主要檔案：`src/bankroll/policies.py`
- 風控：`src/bankroll/controls.py`

新增回測欄位
- 交易層：`src/backtest/engine.py`
- 聚合層：`src/backtest/metrics.py`

新增優化參數
- 主要檔案：`src/optimizer/grid_search.py`

新增警示通道
- 主要檔案：`src/alerts/telegram.py`
- 客戶端：`src/alerts/telegram_client.py`

## 5. 變更守則

- 生產邏輯放在 `src/`，notebook 只做探索。
- 保持 full-time settlement（90+傷停）規則，不納入加時與 PK。
- 新功能預設要有型別註解與測試。
- 回測與報告需誠實揭露 NON_HKJC 限制，不誇大獲利結論。
