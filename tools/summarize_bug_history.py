"""Count reviewed defect families, never infer incident rates from log frequency.

The catalogue and generated reports belong in runtime/ because evidence may
refer to private failure folders. This command does not contact the game.
"""
import argparse
from collections import Counter
from html import escape
import json
from pathlib import Path


LABELS={
    'text_roi':'文字裁切与预处理',
    'parsing_validation':'字段解析、重读与多余校验',
    'visual_geometry':'非文字视觉几何',
    'navigation_state':'页面导航与前置状态',
    'pagination_lookup':'分页与目标定位',
    'process_lifecycle':'进程、中断与生命周期',
    'data_ui':'数据与界面契约',
    'distribution_windows':'分发与 Windows 兼容',
}


def summarize(catalog,root):
    items=catalog['items']+catalog.get('excluded',[])+catalog.get('unknown',[]);seen=set()
    for item in items:
        if item['id'] in seen:raise ValueError('Duplicate defect ID: '+item['id'])
        seen.add(item['id'])
        if item['kind']=='historical':
            if item['category'] not in LABELS:raise ValueError('Unknown category')
            if not item.get('evidence') or not item.get('trigger') or not item.get('root_cause'):
                raise ValueError('Historical defects need evidence, trigger and root cause')
    for item in items+catalog.get('escapes',[])+catalog.get('new_findings',[]):
        for ref in item.get('evidence',[]):
            path=(root/ref['path']).resolve()
            if not path.is_relative_to(root.resolve()):raise ValueError('Evidence is outside project')
            lines=path.read_text(encoding='utf-8-sig').splitlines()
            if type(ref['line']) is not int or not 1<=ref['line']<=len(lines):raise ValueError('Invalid evidence line')
    historical=[x for x in items if x['kind']=='historical']
    counts=Counter(x['category'] for x in historical);total=len(historical)
    text_count=sum(counts[k] for k in ('text_roi','parsing_validation'))
    return {'date':catalog['metadata']['audit_date'],'unit':'reviewed_independent_defect_family',
        'not_incidence_rate':True,'historical_total':total,
        'categories':[{'key':k,'label':v,'count':counts[k],
                       'percent':round(100*counts[k]/total,2) if total else 0} for k,v in LABELS.items()],
        'text_recognition_chain':{'count':text_count,'percent':round(100*text_count/total,2) if total else 0},
        'visual_geometry_separate':counts['visual_geometry'],
        'excluded':sum(x['kind']=='excluded' for x in items),'unknown':sum(x['kind']=='unknown' for x in items),
        'new_audit_findings_not_in_historical_total':len(catalog.get('new_findings',[])),
        'escape_mechanisms_not_in_historical_total':len(catalog.get('escapes',[]))}


def render(catalog,summary):
    def e(value):return escape(str(value))
    def evidence(row):
        return '<br>'.join(e(f"{ref['path']}:{ref['line']}") for ref in row.get('evidence',[])) or '历史对话；证据尚不完整'
    category_rows=''.join(f"<tr><td>{e(r['label'])}</td><td>{r['count']}</td><td>{r['percent']:.2f}%</td></tr>" for r in summary['categories'])
    details=''.join(f"<tr><td>{e(r['id'])}<br>{e(LABELS[r['category']])}</td><td>{e(r['title'])}</td>"
                    f"<td>{e(r['trigger'])}</td><td>{e(r['root_cause'])}<small>{evidence(r)}</small></td></tr>"
                    for r in catalog['items'] if r['kind']=='historical')
    excluded=''.join(f"<li><b>{e(r['title'])}</b>：{e(r.get('note') or r.get('root_cause'))}</li>"
                     for r in catalog['items']+catalog.get('excluded',[])+catalog.get('unknown',[]) if r['kind']!='historical')
    escapes=''.join(f"<li><b>{e(r['title'])}</b>：{e(r['mechanism'])}</li>" for r in catalog.get('escapes',[]))
    findings=''.join(f"<li><b>{e(r['title'])}</b>：{e(r['root_cause'])}<br>{e(r['fix'])}</li>" for r in catalog.get('new_findings',[]))
    limitations=''.join(f'<li>{e(x)}</li>' for x in catalog['metadata']['limitations'])
    sources=''.join(f'<li><a href="{e(s["url"])}">{e(s["title"])}</a>：{e(s["finding"])}</li>' for s in catalog.get('external_sources',[]))
    verification=''.join(f'<li>{e(v)}</li>' for v in catalog.get('verification',[]))
    pending=''.join(f'<li><b>{e(v["title"])}</b>（{e(v["status"])}）：{e(v["detail"])}</li>' for v in catalog.get('pending_risks',[]))
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>故障根因审计</title>
<style>body{{font:16px/1.7 system-ui,"Microsoft YaHei",sans-serif;background:#f5f5f7;color:#1d1d1f;margin:0}}main{{max-width:1200px;margin:48px auto;padding:0 28px}}section{{background:white;border:1px solid #e5e5ea;border-radius:18px;padding:26px;margin:24px 0}}h1{{font-size:32px}}h2{{font-size:23px}}table{{width:100%;border-collapse:collapse;text-align:left}}th,td{{border-bottom:1px solid #e5e5ea;padding:12px;vertical-align:top}}th{{background:#f5f5f7}}small{{display:block;color:#626270;font-size:12px;overflow-wrap:anywhere;margin-top:8px}}li{{margin:9px 0}}a{{color:#0066cc}}.note{{color:#626270}}.scroll{{overflow-x:auto}}</style>
<main><h1>圣遗物工坊：故障根因审计</h1><p class="note">{e(summary['date'])} · 只统计有证据的独立缺陷族 · 不代表用户故障率</p>
<section><p>历史确认 {summary['historical_total']} 项；文字识别链 {summary['text_recognition_chain']['count']} 项（{summary['text_recognition_chain']['percent']:.2f}%）。材料星级几何单列。并无证据表明这些都能通过更换 OCR 模型解决。</p>
<p>{e(catalog['metadata']['counting_unit'])}</p><table><thead><tr><th>主因分类</th><th>数量</th><th>构成比例</th></tr></thead><tbody>{category_rows}</tbody></table><p>{e(catalog['metadata']['not_incidence_rate'])}</p></section>
<section><h2>按触发条件查看全部历史缺陷</h2><div class="scroll"><table><thead><tr><th>编号／分类</th><th>问题</th><th>触发条件</th><th>根因／本地证据</th></tr></thead><tbody>{details}</tbody></table></div></section>
<section><h2>这次新增修复</h2><p>以下来自本轮代码审计，没有混入历史事故分母。</p><ul>{findings}</ul><h2>为什么问题曾漏过验收</h2><ul>{escapes}</ul><h2>验证范围</h2><ul>{verification}</ul></section>
<section><h2>没有计入缺陷统计的反馈</h2><ul>{excluded}</ul><h2>仍需覆盖的条件</h2><ul>{pending}</ul><h2>统计限制</h2><ul>{limitations}</ul></section>
<section><h2>相关项目源码对照</h2><ul>{sources}</ul><p class="note">原始截图、账号库存和控制器凭据不包含在此报告中。证据文件名仅为本地追溯索引。</p></section></main></html>'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog',type=Path,required=True)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();catalog=json.loads(args.catalog.read_text(encoding='utf-8-sig'))
    summary=summarize(catalog,args.root)
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (args.output/'report.html').write_text(render(catalog,summary),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':main()
