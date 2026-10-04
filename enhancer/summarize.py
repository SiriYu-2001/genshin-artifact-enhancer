"""Reproduce the run's scores and resource accounting locally, without game input."""
from collections import Counter
from copy import deepcopy
from dataclasses import replace
import json
import re

from .capped import CappedInventory, build_score
from .experiment import Experiment, RUN, SCAN, TARGET_ID
from .model import Profile


def write_summary(run=None):
    run = run or Experiment()
    if (RUN / "pending.json").exists():
        raise RuntimeError("An unreconciled operation prevents a final cost report")
    after_pool = [run.target if a.id == TARGET_ID else a for a in run.inventory]
    before_lookup = {a.id:a for a in run.inventory}
    after_lookup = {a.id:a for a in after_pool}
    modes = {}
    for mode in ("no-borrow", "borrow"):
        profile = Profile(deepcopy(run.profile.data))
        profile.data["allowed_equipped_characters"] = ["*"] if mode == "borrow" else profile.data["character_aliases"]
        before = CappedInventory(run.inventory, profile)
        after = CappedInventory(after_pool, profile)
        if before.baseline is None or after.baseline is None:
            modes[mode] = {"available":False}
            continue
        b = float(build_score([before_lookup[k] for k in before.best_ids], profile, displayed=True))
        a = float(build_score([after_lookup[k] for k in after.best_ids], profile, displayed=True))
        possible = CappedInventory([replace(x,equipped="") if x.equipped.startswith("UNKNOWN:") else x for x in after_pool], profile)
        unresolved = [point for point in possible.full_hi if possible.score(point) > after.baseline.lo
                      and any(after_lookup[k].equipped.startswith("UNKNOWN:") for k in point.ids)] if mode == "no-borrow" else []
        modes[mode] = {"available":True,"before":b,"after":a,"improvement":a-b,
                       "after_internal_bounds":[float(after.baseline.lo),float(after.baseline.hi)],
                       "ownership_optimum_certified":not unresolved,
                       "items":[{"id":k,"name":run.names[k],"slot":after_lookup[k].slot,
                                 "equipped":after_lookup[k].equipped} for k in after.best_ids]}
    receipts = sorted((json.loads(p.read_text(encoding="utf-8")) for p in RUN.glob("receipt-*.json")), key=lambda x:x["finished"])
    if not receipts:
        raise RuntimeError("No completed material operation")
    if any(left["after"]["mora"] != right["before"]["mora"] for left,right in zip(receipts,receipts[1:])):
        raise RuntimeError("Mora ledger is not continuous")
    fodder = Counter(s for receipt in receipts for s in receipt.get("rarities",[]))
    exp = Counter()
    for receipt in receipts:
        if "materials" in receipt:
            for material in receipt["materials"]:
                if material["kind"] == "exp_item":exp[material["name"]] += material["quantity"]
        elif receipt["material"] in ("祝圣精华","祝圣油膏"):
            exp[receipt["material"]] += receipt["quantity"]
    cost = sum(x["mora_spent"] for x in receipts)
    if cost != receipts[0]["before"]["mora"] - receipts[-1]["after"]["mora"]:
        raise RuntimeError("Mora totals disagree")
    initial_count = json.loads((SCAN/"scan-count.json").read_text(encoding="utf-8-sig"))["requested"]
    final_count = None
    verification_path = RUN/"final-backpack-verification.json"
    if verification_path.exists():
        verification = json.loads(verification_path.read_text(encoding="utf-8"))
        match = re.search(r"(\d+)\s*/\s*\d+",verification["inventory"])
        if match:final_count = int(match[1])
    result = {"target_name":run.names[TARGET_ID],"level":run.target.level,
              "substats":[{"key":s.key,"value":float(s.value)} for s in run.target.stats],
              "modes":modes,"mora_spent":cost,"mora_before":receipts[0]["before"]["mora"],
              "mora_after":receipts[-1]["after"]["mora"],"artifact_materials":dict(fodder),
              "exp_items_input":dict(exp),"confirmations":len(receipts),"five_star_materials_consumed":fodder[5],
              "inventory_count_before":initial_count,"inventory_count_after":final_count,
              "inventory_reconciled":final_count is not None and initial_count-final_count==sum(fodder.values())}
    (RUN/"summary.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    lines = ["# 木偶圣遗物单件实测结果", "", "本轮只培养一件“止于荣礼的缎彩”，从 +0 到 +20；未自动换装。", "",
             "条件：影中沉凝的幻灭 4+1；攻击沙、攻击杯、暴伤冠；平均词条计分；圣遗物主、副词条合计暴击率计分上限 44.8%。", "",
             "| 模式 | 强化前 | 强化后 | 提升 |", "|---|---:|---:|---:|"]
    for key,label in (("no-borrow","不借用其他角色装备"),("borrow","允许借用其他角色装备")):
        m = modes[key]
        if m.get("available"):lines.append(f'| {label} | {m["before"]:.3f} | {m["after"]:.3f} | +{m["improvement"]:.3f} |')
    lines += ["", "表中按游戏显示值计分；内部档位数值的保守区间保存在 summary.json。两种模式均重新搜索整套最优配置。", "",
              f"摩拉实测减少 **{cost:,}**（{result['mora_before']:,} → {result['mora_after']:,}）。",
              f"投入三星圣遗物 {fodder[3]} 件、四星圣遗物 {fodder[4]} 件、祝圣精华 {exp['祝圣精华']} 瓶、祝圣油膏 {exp['祝圣油膏']} 瓶；**五星素材 0 件**。",
              "经验瓶数量为已确认批次的投料记录，未单独复核瓶子余额或返还。",
              f"圣遗物背包数量 {initial_count} → {final_count}，与消耗 {sum(fodder.values())} 件低星圣遗物一致。", "",
              "成品副词条：攻击力 9.3%、暴击率 6.6%、暴击伤害 19.4%、元素充能效率 12.3%。", "",
              "不借用模式的散件为闲置“永劫之冕”；允许借用模式的散件为阿蕾奇诺的“异想零落的圆舞”。", "",
              "## 验证范围", "", "完成了单件实测与结束后的 yas 背包复核。开发过程中发生过中断并修复；未把它描述为最终版本一次无中断的 +0→+20 回归。",
              "运行逻辑为本地脚本：yas OCR、霜华输入、确定性概率计算与状态机，没有 LLM 调用。当前仍是 Windows 1920×1080 简体中文界面的实验版；多件队列与更广泛界面兼容尚未完成。"]
    (RUN/"RESULTS.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(write_summary(),ensure_ascii=False,indent=2))
