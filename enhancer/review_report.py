"""Explain policy deferrals separately from unconfirmed game operations."""


def review_item(item,*,demand=None,threshold=None):
    lo=item.get('probability_lower');hi=item.get('probability_upper');reason=item.get('reason','')
    if lo is not None and hi is not None:
        code='probability_boundary'
        explanation='显示数值的取整使真实属性有多种可能，提升概率区间跨过了强化阈值。'
        suggestion='本件保留。可调整配置中的概率阈值后另开一轮；重复扫描未必能消除游戏显示取整。'
    elif 'guarantee state' in reason:
        code='guarantee_unknown';explanation='定制或特殊强化的保底状态不足，暂不能按普通圣遗物概率判断。'
        suggestion='本件保留，可手动处理；不会使用普通强化概率代替其保底机制。'
    elif 'four known stat' in reason:
        code='missing_substat';explanation='缺少完整的四条副属性（含待激活属性）。'
        suggestion='重新扫描这一筛选范围，或导入包含第四条预览属性的新库存。'
    else:
        code='observation_unsupported';explanation='属性或强化状态不能通过当前概率模型校验。'
        suggestion='核对该物品的等级和副属性，更新库存；仍不一致时提供扫描记录。'
    return {'id':item.get('id'),'name':item.get('name',item.get('id','未知物品')),'level':item.get('level'),
            'demand':demand,'code':code,'reason':explanation,'suggestion':suggestion,
            'probability_lower':lo,'probability_upper':hi,'threshold':threshold,'technical_reason':reason}


def review_items(data):
    result=[review_item(d,threshold=data.get('threshold')) for d in data.get('deferred',[])]
    blocked=set(data.get('blocked_demands',[]))
    for row in data.get('demands',[]):
        threshold=row.get('threshold',row.get('effective_profile',{}).get('threshold'))
        result.extend(review_item(d,demand=row.get('name',row['id']),threshold=threshold) for d in row.get('deferred',[]))
        if row['id'] in blocked:
            result.append({'id':row['id'],'name':row.get('name',row['id']),'code':'missing_build',
                           'reason':'当前库存无法建立满足套装、主属性、最低充能和装备归属限制的起始配装。',
                           'suggestion':'补充库存或调整该角色约束后重新计算；不会跳过硬性约束凑一套。'})
    return result
