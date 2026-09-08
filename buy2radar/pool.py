from __future__ import annotations

from .model import AnalysisResult, Stage, StructureStatus

HOUR_MS = 3600000


class Candidate:
    def __init__(self, res: AnalysisResult):
        self.res = res
        self.is_member = False
        self.in_top = False
        self.rank = 0
        self.change = ""
        self.wait_reason = ""


def _qualifies(res: AnalysisResult, mode: str, cfg: dict) -> tuple[bool, str]:
    if not res.ok or res.event is None:
        return False, "no_signal"
    sc = cfg["score"]
    pool = cfg["pool"]
    if res.structure_status not in (StructureStatus.VALID, StructureStatus.CONFIRMED):
        return False, "struct"
    if res.turnover24h < float(pool.get("minTurnoverPool", 1e6)):
        return False, "liq"
    score_line = sc["enterScore"] if mode == "enter" else sc["exitScore"]
    if res.buy2_score < float(score_line):
        return False, "score"
    runup_cap = (
        float(pool.get("enterMaxRunupPct", 25.0))
        if mode == "enter"
        else float(pool.get("exitMaxRunupPct", 40.0))
    )
    if res.gain_pct > runup_cap:
        return False, "runup"
    dist_line = (
        float(pool.get("enterDistanceAtr", 1.5))
        if mode == "enter"
        else float(pool.get("exitDistanceAtr", 2.0))
    )
    if res.distance_atr != res.distance_atr:
        return False, "dist"
    if res.distance_atr > dist_line:
        return False, "dist"
    return True, ""


def _tier_bonus(res: AnalysisResult, table: dict) -> float:
    try:
        key = res.trading_value_status.name
    except AttributeError:
        key = ""
    try:
        return float(table.get(key, 0.0))
    except (TypeError, ValueError):
        return 0.0


def _sort_key(c: Candidate, mem_bonus: float, tier_table: dict | None = None) -> float:
    bonus = mem_bonus if c.is_member else 0.0
    return c.res.final_score + bonus + _tier_bonus(c.res, tier_table or {})


def plan_pool(
    prev_state: dict, results: list[AnalysisResult], now_ms: int, cfg: dict
) -> tuple[list[dict], list[Candidate]]:
    pool_cfg = cfg["pool"]
    rank_cfg = cfg.get("rank", {})
    mem_bonus = float(rank_cfg.get("membershipBonus", 3.0))
    tier_table = rank_cfg.get("tierBonus", {}) or {}
    cooldown_ms = float(pool_cfg.get("removeCooldownHours", 12)) * HOUR_MS
    per_base_mode = str(
        pool_cfg.get(
            "perBaseMode", cfg.get("universe", {}).get("perBaseMode", "best")
        )
    )
    top_n = int(pool_cfg.get("topN", 10))

    prev = dict(prev_state)

    def was_member(inst_id: str) -> bool:
        r = prev.get(inst_id)
        return bool(r and r.get("is_member"))

    cands: list[Candidate] = []
    for res in results:
        c = Candidate(res)
        if was_member(res.inst_id):
            c.is_member = True
        cands.append(c)

    for c in cands:
        res = c.res
        if c.is_member:
            keep, code = _qualifies(res, "keep", cfg)
            if keep:
                c.change = "keep"
            else:
                c.is_member = False
                c.change = code or _remove_reason(res, cfg)
                c.wait_reason = "removed"
                res.stage = Stage.REMOVED
        else:
            ok, code = _qualifies(res, "enter", cfg)
            if ok:
                rec = prev.get(res.inst_id)
                removed_ms = rec.get("removed_ms") if rec else None
                if removed_ms and now_ms - removed_ms < cooldown_ms:
                    c.wait_reason = "cooldown"
                else:
                    c.wait_reason = "eligible"
            else:
                c.wait_reason = "no:" + (code or "n/a")

    active = [c for c in cands if c.is_member or c.wait_reason == "eligible"]

    selected: list[Candidate] = []
    used_bases: set[str] = set()
    for c in sorted(active, key=lambda x: _sort_key(x, mem_bonus, tier_table), reverse=True):
        if per_base_mode == "best" and c.res.base in used_bases:
            continue
        selected.append(c)
        used_bases.add(c.res.base)
        if len(selected) >= top_n:
            break

    chosen: set[str] = set(c.res.inst_id for c in selected)

    for c in cands:
        res = c.res
        if res.inst_id in chosen:
            c.in_top = True
            res.stage = Stage.TOP10
            if not c.is_member:
                c.change = "entered"
                c.is_member = True
        else:
            if c.is_member:
                c.change = "displaced"
                c.is_member = False
                c.wait_reason = "displaced"
                res.stage = Stage.REMOVED
            elif res.ok and res.event is not None and c.wait_reason == "eligible":
                res.stage = Stage.CANDIDATE

    ordered = sorted(cands, key=lambda x: _sort_key(x, mem_bonus, tier_table), reverse=True)
    for i, c in enumerate(ordered):
        c.rank = i + 1

    rows: list[dict] = []
    now = now_ms
    seen: set[str] = set()
    for c in ordered:
        seen.add(c.res.inst_id)
        rec = prev.get(c.res.inst_id) or {}
        if c.is_member:
            rows.append(
                {
                    "inst_id": c.res.inst_id,
                    "is_member": True,
                    "in_top": c.in_top,
                    "entered_ms": rec.get("entered_ms") or now,
                    "removed_ms": None,
                    "last_score": c.res.buy2_score,
                    "last_final": c.res.final_score,
                    "ts": now,
                }
            )
        elif rec:
            rows.append(
                {
                    "inst_id": c.res.inst_id,
                    "is_member": False,
                    "in_top": False,
                    "entered_ms": None,
                    "removed_ms": rec.get("removed_ms") or now,
                    "last_score": c.res.buy2_score if c.res.ok else 0.0,
                    "last_final": c.res.final_score if c.res.ok else 0.0,
                    "ts": now,
                }
            )
    for k, rec in prev.items():
        if k not in seen:
            rows.append(
                {
                    "inst_id": k,
                    "is_member": False,
                    "in_top": False,
                    "entered_ms": None,
                    "removed_ms": now,
                    "last_score": rec.get("last_score", 0.0),
                    "last_final": rec.get("last_final", 0.0),
                    "ts": now,
                }
            )

    return rows, ordered
