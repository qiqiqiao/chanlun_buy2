from __future__ import annotations

import copy
import json
import os

DEFAULT_CONFIG: dict = {
    "universe": {
        "instTypes": ["SPOT", "SWAP"],
        "quoteCcy": "USDT",
        "stableBases": [
            "USDC",
            "FDUSD",
            "TUSD",
            "DAI",
            "USDP",
            "PYUSD",
            "USDE",
            "BUSD",
            "XUSD",
            "EURC",
            "AEUR",
        ],
        "excludeBases": [],
        "forceBases": [],
        "minSpotTurnover24h": 300000.0,
        "minSwapTurnover24h": 500000.0,
        "maxInstruments": 500,
        "perBaseMode": "best",
    },
    "data": {
        "bar": "1Dutc",
        "historyBars": 320,
        "maxCandlesPerReq": 300,
        "requestIntervalSec": 0.06,
        "threads": 8,
        "retries": 3,
        "timeoutSec": 20,
        "minCandles": 220,
    },
    "chan": {
        "atrPeriod": 14,
        "macdFast": 12,
        "macdSlow": 26,
        "macdSignal": 9,
        "biMinMergedGap": 2,
        "fractalConfirmerClosed": True,
        "waveReboundFrac": 0.5,
        "minBounceAtr": 0.6,
        "requireBuy1Divergence": False,
    },
    "buy2": {
        "maxAgeDays": 60,
        "minAnchorBarsAfter": 1,
        "maxDipRecoverAtr": 1.0,
        "runupValueCapPct": 25.0,
        "maxBarsB1toB2": 0,
        "maxB2GapAtr": 0.0,
        "requireConfirmCloseAboveB1": False,
    },
    "zones": {
        "optimalAtr": 0.5,
        "goodAtr": 1.0,
        "watchAtr": 1.5,
        "maxAtr": 2.0,
    },
    "score": {
        "enterScore": 70.0,
        "exitScore": 55.0,
        "tradingValueMinScore": 40.0,
        "weights": {
            "structure": 30,
            "distance": 25,
            "gain": 15,
            "freshness": 10,
            "liquidity": 10,
            "volumeChange": 10,
        },
        "freshnessHalfLifeDays": 10,
        "ageScorePoints": [
            [0.0, 100.0],
            [15.0, 100.0],
            [30.0, 70.0],
            [45.0, 45.0],
            [60.0, 25.0],
            [80.0, 0.0],
        ],
        "liquidityTurnMin": 2_000_000.0,
        "liquidityTurnMax": 5_000_000_000.0,
        "baselineDays": 5,
        "tables": {
            "distancePoints": [
                [-4.0, 0.0],
                [-1.0, 30.0],
                [-0.5, 70.0],
                [0.0, 100.0],
                [0.5, 100.0],
                [1.0, 78.0],
                [1.5, 55.0],
                [2.0, 32.0],
                [3.0, 0.0],
            ],
            "gainPoints": [
                [0.0, 100.0],
                [0.25, 100.0],
                [0.5, 90.0],
                [0.75, 70.0],
                [1.0, 50.0],
                [1.5, 25.0],
                [2.0, 10.0],
                [2.5, 0.0],
            ],
            "volumePoints": [
                [0.0, 0.0],
                [0.3, 15.0],
                [0.6, 40.0],
                [0.8, 60.0],
                [1.0, 72.0],
                [1.5, 90.0],
                [2.0, 98.0],
                [3.0, 100.0],
                [6.0, 100.0],
            ],
        },
    },
    "rank": {
        "liquidityWeight": 0.2,
        "membershipBonus": 3.0,
        "replaceBias": 0.0,
        "tierBonus": {
            "EXCELLENT": 3.0,
            "GOOD": 1.0,
            "WATCH": 0.0,
            "EXTENDED": -3.0,
        },
    },
    "pool": {
        "topN": 10,
        "perBaseMode": "best",
        "enterDistanceAtr": 1.5,
        "exitDistanceAtr": 2.0,
        "removeCooldownHours": 12,
        "minTurnoverPool": 1_000_000.0,
        "maxDisplay": 20,
        "enterMaxRunupPct": 25.0,
        "exitMaxRunupPct": 25.0,
    },
    "monitor": {
        "fullScanMinutes": 15,
        "lightRefreshSeconds": 60,
        "candidateRefresh": 40,
        "quiet": False,
    },
    "backtest": {
        "forwardDays": [5, 10, 20],
        "minScoreGrid": [55, 60, 65, 70, 75, 80],
        "distanceGrid": [1.0, 1.5, 2.0, 2.5],
        "workers": 0,
        "feeBpsPerSide": 10.0,
    },
    "state": {
        "dir": "data/state",
        "dbFile": "buy2.db",
        "candleLimit": 1000,
    },
}


def deep_merge(base: dict, overlay: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (overlay or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(path: str | None = None) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            user = json.load(f)
        cfg = deep_merge(cfg, user)
    return cfg


def save_config(cfg: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")
