"""Exploratory follow-up diagnostics, deliberately separate from the frozen test."""
import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent / 'outputs'
DATA=Path(__file__).resolve().parent / 'data'
def main():
    d=pd.read_csv(DATA/'sources/right_fit/results/task_level.tsv',sep='\t')
    core=d[d.harness.isin(['OpenHands','DSH','PI','openJiuwen'])].copy()
    assert len(core)==5640 and not core.cost_usd.isna().any()
    g=core.groupby(['model','benchmark','harness']).agg(score=('reward','mean'),cost=('cost_usd','mean')).reset_index()
    rows=[]; quality_only=[]
    for model,a in g.groupby('model'):
        for bench in sorted(a.benchmark.unique()):
            train=a[a.benchmark!=bench].copy()
            train['relative_cost']=train.cost/train.groupby('benchmark').cost.transform('mean')
            profiles=train.groupby('harness').agg(score=('score','mean'),relative_cost=('relative_cost','mean'))
            allowed=profiles[profiles.score>=profiles.score.max()-.05];choice=allowed.relative_cost.idxmin()
            test=a[a.benchmark==bench].set_index('harness');ref=test[test.score>=test.score.max()-.05].cost.idxmin()
            rows.append({'model':model,'heldout_benchmark':bench,'selected':choice,'hindsight_reference':ref,
                'selected_score':float(test.loc[choice,'score']),'reference_score':float(test.loc[ref,'score']),
                'gap_to_best_score':float(test.score.max()-test.loc[choice,'score']),
                'cost_ratio_to_reference':float(test.loc[choice,'cost']/test.loc[ref,'cost'])})
            qc=profiles.score.idxmax(); quality_only.append({'model':model,'heldout_benchmark':bench,'selected':qc,
                'quality_gap':float(test.score.max()-test.loc[qc,'score'])})
    result={'status':'Exploratory descriptive domain transfer; three domains, not a second blind test. Point-estimate constraints, not statistical noninferiority.',
            'rule':'Train two domains: equal mean reward, equal mean relative cost normalized by domain mean. Select lowest relative cost within 0.05 of best reward. Reference uses heldout outcomes and is hindsight-only.',
            'rows':rows,'quality_margin_violations':sum(z['gap_to_best_score']>.05 for z in rows),'comparisons':len(rows),
            'quality_only_control':{'mean_quality_gap':float(np.mean([z['quality_gap'] for z in quality_only])),
                'maximum_quality_gap':max(z['quality_gap'] for z in quality_only),'rows':quality_only}}
    (ROOT/'cost-aware-transfer-diagnostic.json').write_text(json.dumps(result,indent=2))
    v=pd.read_csv(ROOT/'repeat-variation.csv');ids=sorted(v.task.unique())
    ratios=[v.loc[v.task==t,'max_min_ratio'].to_numpy() for t in ids];rng=np.random.default_rng(1706)
    boots=[np.median(np.concatenate([ratios[i] for i in rng.integers(len(ids),size=len(ids))])) for _ in range(2000)]
    (ROOT/'repeat-variation-ci.json').write_text(json.dumps({'task_clusters':len(ids),'bootstrap_draws':2000,
        'median_ratio_task_bootstrap_95CI':np.quantile(boots,[.025,.975]).tolist()},indent=2))
    print('Exploratory transfer violations:',result['quality_margin_violations'],'/',len(rows))
    print('Quality-only control mean gap:',result['quality_only_control']['mean_quality_gap'])

if __name__=='__main__':main()
