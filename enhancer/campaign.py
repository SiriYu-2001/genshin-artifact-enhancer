"""One inventory, multiple demands, explicit equipment and coexistence policies.

Allocation is priority-ordered greedy, not a global multi-character optimum.
All reports/planning are pure; game execution lives in campaign_runtime.py.
"""
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import json
import re

from .model import Profile
from .report import select_build
from .capped import build_score
from .batch import replan


@dataclass
class Demand:
    id: str
    profile: Profile


@dataclass
class Campaign:
    demands: list[Demand]
    equipment: str
    allocation: str
    scenarios: list[list[str]] | None
    reserved_ids: list[str]
    scenario_names: list[str] | None = None

    @classmethod
    def load(cls, path):
        path=Path(path)
        data=json.loads(path.read_text(encoding='utf-8-sig'))
        if data.get('version')!=1:raise ValueError('Campaign version must be 1')
        equipment=data.get('equipment','protected')
        allocation=data.get('allocation','priority')
        if equipment not in ('protected','borrow'):raise ValueError('Unknown equipment policy')
        if allocation not in ('priority','independent'):raise ValueError('Unknown allocation policy')
        demands=[]
        for entry in data['demands']:
            identifier=entry['id']
            if not isinstance(identifier,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',identifier):
                raise ValueError('Demand ID must be a short filename-safe identifier')
            profile=Profile.load(path.parent/entry['profile'])
            if profile.data.get('artifact_crit_rate_cap') is None:
                raise ValueError('Campaign requires an explicit artifact CR cap for each demand')
            demands.append(Demand(identifier,profile))
        ids=[d.id for d in demands]
        if not ids or len(ids)!=len(set(ids)):raise ValueError('Demand IDs must be unique and nonempty')
        scenarios=data.get('scenarios')
        scenario_names=data.get('scenario_names')
        if scenarios is not None:
            if not isinstance(scenarios,list) or not scenarios:raise ValueError('Provide nonempty scenarios or omit them')
            normalized=[];labels=[]
            teams=data.get('teams',{})
            if not isinstance(teams,dict):raise ValueError('teams must map names to demand ID lists')
            for index,scenario in enumerate(scenarios):
                if isinstance(scenario,dict):
                    team_names=scenario.get('teams',[])
                    if not isinstance(team_names,list) or any(t not in teams for t in team_names):
                        raise ValueError('Unknown team in scenario')
                    members=scenario.get('demands',[])
                    if not isinstance(members,list):raise ValueError('Scenario demands must be a list')
                    members=list(members)
                    for team in team_names:
                        if not isinstance(teams[team],list):raise ValueError('A team must list demand IDs')
                        members.extend(teams[team])
                    normalized.append(members)
                    labels.append(scenario.get('name',f'scenario-{index+1}'))
                else:
                    normalized.append(scenario);labels.append(f'scenario-{index+1}')
            scenarios=normalized
            scenario_names=scenario_names or labels
            if len(scenario_names)!=len(scenarios) or any(not isinstance(x,str) or not x for x in scenario_names):
                raise ValueError('Invalid scenario names')
            covered=set()
            for scenario in scenarios:
                if (not isinstance(scenario,list) or not scenario or any(not isinstance(x,str) for x in scenario)
                        or len(scenario)!=len(set(scenario)) or set(scenario)-set(ids)):
                    raise ValueError('Invalid scenario demand IDs')
                covered.update(scenario)
            if covered!=set(ids):raise ValueError('Every demand must appear in a scenario')
        reserved=data.get('reserved_ids',[])
        if not isinstance(reserved,list) or any(not isinstance(x,str) for x in reserved):
            raise ValueError('reserved_ids must be a list of inventory IDs')
        return cls(demands,equipment,allocation,scenarios,reserved,scenario_names)

    def conflicts(self, left, right):
        if left==right:return False
        if self.scenarios is not None:
            return any(left in s and right in s for s in self.scenarios)
        profiles={d.id:d.profile.data for d in self.demands}
        # Character aliases matter: 木偶 and 桑多涅 are alternative demands.
        names=lambda p:set(p.get('character_aliases',[]))|{p['character']}
        return not bool(names(profiles[left]) & names(profiles[right]))

    def snapshot(self, directory):
        directory=Path(directory)
        directory.mkdir(parents=True,exist_ok=True)
        entries=[]
        for demand in self.demands:
            filename=f'{demand.id}.json'
            (directory/filename).write_text(json.dumps(demand.profile.data,ensure_ascii=False,indent=2),encoding='utf-8')
            entries.append({'id':demand.id,'profile':filename})
        path=directory/'campaign.json'
        path.write_text(json.dumps({'version':1,'equipment':self.equipment,'allocation':self.allocation,
                                   'scenarios':self.scenarios,'scenario_names':self.scenario_names,
                                   'reserved_ids':self.reserved_ids,'demands':entries},
                                  ensure_ascii=False,indent=2),encoding='utf-8')
        return path


def effective_profile(campaign, demand, claims):
    p=Profile(deepcopy(demand.profile.data))
    p.data['allowed_equipped_characters']=(['*'] if campaign.equipment=='borrow' else
        p.data.get('character_aliases',[p.data['character']]))
    blocked={item:owners for item,owners in claims.items()
             if campaign.allocation=='priority' and any(campaign.conflicts(demand.id,other) for other in owners)}
    p.data['reserved_ids']=sorted(set(p.data.get('reserved_ids',[]))|set(campaign.reserved_ids)|set(blocked))
    return p,blocked


def allocate(inventory, campaign, names,bootstrap_plans=None):
    """Reserve mature builds from highest to lowest priority, retaining item IDs."""
    reserved=set(campaign.reserved_ids)
    for demand in campaign.demands:reserved.update(demand.profile.data.get('reserved_ids',[]))
    unknown=reserved-{a.id for a in inventory}
    if unknown:raise ValueError(f'Reservation IDs not present in this scan: {sorted(unknown)}')
    claims={}
    rows=[]
    for demand in campaign.demands:
        profile,blocked=effective_profile(campaign,demand,claims)
        build=select_build(inventory,profile)
        bootstrap=None
        if build is None:
            from .bootstrap import plan as bootstrap_plan
            bootstrap=bootstrap_plan(inventory,profile,names,committed=(bootstrap_plans or {}).get(demand.id))
        unreserved,_=effective_profile(campaign,demand,{})
        independent=select_build(inventory,unreserved) if blocked else build
        ids=[a.id for a in build] if build else []
        if bootstrap:ids=bootstrap['items']
        preview_build=build or ([a for a in inventory if a.id in ids] if bootstrap else [])
        row={'id':demand.id,'character':profile.data['character'],'name':profile.data.get('name',demand.id),
             'status':'ready' if build else 'bootstrap' if bootstrap else 'missing_mature_build',
             'bootstrap':bootstrap,
             'score':float(build_score(build,profile,displayed=True)) if build else None,
             'independent_score':float(build_score(independent,unreserved,displayed=True)) if independent else None,
             'items':[{'id':a.id,'name':names[a.id],'slot':a.slot,'set_key':a.set_key,'main':a.main,
                       'level':a.level,'equipped':a.equipped,'substats':[{'key':s.key,'value':float(s.value)} for s in a.stats]}
                      for a in preview_build],
             'blocked_by_higher_demands':blocked,'effective_profile':profile.data}
        from .capped import main_energy
        row['artifact_energy_recharge_min']=profile.data.get('artifact_energy_recharge_min',0)
        row['artifact_energy_recharge_total']=float(sum(main_energy(a)+sum(s.value for s in a.stats if s.key=='enerRech_' and not s.pending) for a in build)) if build else None
        rows.append(row)
        for identifier in ids:claims.setdefault(identifier,[]).append(demand.id)
    overlaps=[{'artifact_id':identifier,'demands':owners,
               'conflicting_pairs':[[a,b] for i,a in enumerate(owners) for b in owners[i+1:] if campaign.conflicts(a,b)]}
              for identifier,owners in claims.items() if len(owners)>1]
    conflicts=[r for r in overlaps if r['conflicting_pairs']]
    if campaign.allocation=='priority' and conflicts:raise RuntimeError('Allocation invariant violated')
    listed_names={name for d in campaign.demands for name in
                  d.profile.data.get('character_aliases',[d.profile.data['character']])}
    transfers=[]
    for row in rows:
        own=set(row['effective_profile'].get('character_aliases',[row['character']]))
        for item in row['items']:
            if item['equipped'] and item['equipped'] not in own:
                transfers.append({'artifact_id':item['id'],'name':item['name'],'from_character':item['equipped'],
                                  'to_demand':row['id'],'outside_list':item['equipped'] not in listed_names})
    return {'equipment':campaign.equipment,'allocation':campaign.allocation,'demands':rows,
            'shared_items':overlaps,'conflicts':conflicts,'claims':claims,'transfers':transfers,
            'scenarios':campaign.scenarios,'scenario_names':campaign.scenario_names}


def plan(inventory, campaign, names,cache=None,bootstrap_plans=None):
    allocation=allocate(inventory,campaign,names,bootstrap_plans)
    selected=None
    for row in allocation['demands']:
        bucket=cache.setdefault(row['id'],{}) if cache is not None else None
        if row['status']=='bootstrap':
            lookup={a.id:a for a in inventory}
            row['decisions']=[{'id':k,'name':names[k],'level':lookup[k].level,'set_key':lookup[k].set_key,
                'action':'enhance','bootstrap':True,'reason':'bootstrap_mature_build','probability_lower':None}
                for k in row['bootstrap']['pending_items']]
        else:row['decisions']=replan(inventory,Profile(row['effective_profile']),names,cache=bucket) if row['status']=='ready' else []
        eligible=[d for d in row['decisions'] if d['action'] in ('enhance','inspect')]
        row['eligible_count']=len(eligible)
        row['deferred']=[d for d in row['decisions'] if d['action'] in ('reread','unsupported')]
        if eligible and selected is None:
            selected={'demand_id':row['id'],'candidate':eligible[0],'profile':row['effective_profile']}
    allocation['next']=selected
    allocation['blocked_demands']=[r['id'] for r in allocation['demands'] if r['status'] not in ('ready','bootstrap')]
    return allocation


def compare_claims(before, after):
    return [{'artifact_id':key,'before':before.get(key,[]),'after':after.get(key,[])}
            for key in sorted(set(before)|set(after)) if before.get(key,[])!=after.get(key,[])]
