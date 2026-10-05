"""Human-readable assignments for both offline previews and completed runs."""
from pathlib import Path

SLOTS={'flower':'花','plume':'羽','sands':'沙','goblet':'杯','circlet':'冠'}
STATS={'hp':'生命值','hp_':'生命值%','atk':'攻击力','atk_':'攻击力%',
       'def':'防御力','def_':'防御力%','eleMas':'精通','enerRech_':'充能%',
       'critRate_':'暴击率%','critDMG_':'暴击伤害%'}


def cell(value):
    return str(value).replace('|','\\|').replace('\n',' ')


def write_report(result,path):
    preview=result.get('status','offline-preview')=='offline-preview'
    lines=['# '+('多需求配装预览' if preview else '多需求强化结果'),'',
           ('离线计算；没有扫描或操作游戏，没有执行强化或换装。' if preview else
            f"状态：{result['status']}；仅计算配装，没有自动换装。"),
           f"装备策略：{result['equipment']}；分配策略：{result['allocation']}。",'',
           '| 需求（优先级从高到低） | 当前分数 | 忽略需求间冲突时 | 本轮提升 | 达标候选 |',
           '|---|---:|---:|---:|---:|']
    for row in result['demands']:
        scores=[f'{row[k]:.4f}' if row.get(k) is not None else '—' for k in ('score','independent_score','gain')]
        lines.append(f"| {cell(row['name'])} ({row['id']}) | {' | '.join(scores)} | {row.get('eligible_count','—')} |")
    lines+=['','priority 为按优先级依次分配的贪心结果，不保证全局联合最优。independent 允许需求之间争用装备，不是可同时穿戴的保证。',
            '不同角色的评分体系可不同，分数不直接相加。缺少满级基准时先搜索可培养的起始套装；形成真实基准后再启用序贯替换阈值。',
            f"同时使用冲突：{len(result['conflicts'])}；未配齐：{len(result.get('blocked_demands',[]))}。"]
    for row in result['demands']:
        lines+=['',f"## {cell(row['name'])} ({row['id']})",'',
                '| 部位 | 圣遗物 | 等级 / 主属性 | 副词条 | 当前装备者 | 库存 ID |',
                '|---|---|---|---|---|---|']
        for a in sorted(row['items'],key=lambda a:list(SLOTS).index(a['slot'])):
            stats='；'.join(f"{STATS.get(s['key'],s['key'])} {s['value']:g}" for s in a['substats'])
            values=[SLOTS[a['slot']],a['name'],f"+{a['level']} / {STATS.get(a['main'],a['main'])}",
                    stats,a['equipped'] or '闲置',a['id']]
            lines.append('| '+' | '.join(cell(v) for v in values)+' |')
        if row.get('bootstrap'):
            b=row['bootstrap'];lines+=['',f"建立起始套装：预计分数 {b['expected_score_lower_model']:.4f}，充能达标概率估计 {b['feasibility_probability_estimate']:.2%}；{b['samples']} 次模拟；搜索受限：{b['search_limited']}。",b['scope']]
        elif row['status']!='ready':lines+=['','库存中缺少满足约束的完整组合，暂不能建立起始套装。']
    lines+=['','## 让装关系','']
    for t in result['transfers']:
        outside='（名单外）' if t['outside_list'] else ''
        lines.append(f"- {cell(t['from_character'])}{outside} → {t['to_demand']}：{cell(t['name'])}，{t['artifact_id']}")
    if not result['transfers']:lines.append('不需要借用其他角色当前装备。')
    lines+=['','## 同时备齐场景','']
    if result['scenarios']:
        for i,scenario in enumerate(result['scenarios']):
            label=(result['scenario_names'] or [f'scenario-{n+1}' for n in range(len(result['scenarios']))])[i]
            lines.append(f"- {cell(label)}：{', '.join(scenario)}")
    else:lines.append('采用默认规则：不同角色不能共用，同一角色的不同需求允许切换复用。')
    if result['shared_items']:
        lines+=['','## 共享装备','']
        for shared in result['shared_items']:
            status='存在同时使用冲突' if shared['conflicting_pairs'] else '仅在互斥场景复用'
            lines.append(f"- {shared['artifact_id']}：{', '.join(shared['demands'])}；{status}。")
    Path(path).write_text('\n'.join(lines)+'\n',encoding='utf-8')
