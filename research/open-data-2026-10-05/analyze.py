import hashlib, json, os
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.feature_extraction import DictVectorizer
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parent / 'outputs'
DATA=Path(__file__).resolve().parent / 'data'
os.umask(0o077)
SEED=1706
def split(task):
    bucket=int(hashlib.sha256(task.encode()).hexdigest()[:8],16)%10
    return 'test' if bucket<2 else 'validation' if bucket<4 else 'train'

def metric(d,p):
    y=d.target.to_numpy(); err=np.abs(p-y)
    z=d[['config']].copy();z['e']=err;z['y']=y
    return {'n':len(d),'tasks':d.task.nunique(),'WAPE':float(err.sum()/y.sum()),'MAE_million_tokens':float(err.mean()),
            'macro_WAPE':float((z.groupby('config').e.sum()/z.groupby('config').y.sum()).mean()),
            'log1p_MAE':float(np.abs(np.log1p(p)-np.log1p(y)).mean())}

def features(d,prefix=False):
    fields=['config','language']+([k for k in d if k.startswith('p_')] if prefix else [])
    return d[fields].to_dict('records')

def fit_predict(tr,parts,prefix):
    v=DictVectorizer(sparse=False);X=v.fit_transform(features(tr,prefix)); y=np.log1p(tr.target)
    m=RandomForestRegressor(n_estimators=300,min_samples_leaf=15,max_depth=8,random_state=SEED,n_jobs=2)
    m.fit(X,y)
    smear=np.mean(np.exp(y-m.predict(X)))
    preds={key:np.maximum(0,np.exp(m.predict(v.transform(features(d,prefix))))*smear-1) for key,d in parts.items()}
    return preds,dict(sorted(zip(v.get_feature_names_out(),m.feature_importances_.tolist()),key=lambda x:-x[1]))

def paired_bootstrap(test,p0,p1):
    z=pd.DataFrame({'task':test.task.to_numpy(),'e0':np.abs(p0-test.target),'e1':np.abs(p1-test.target)})
    g=z.groupby('task')[['e0','e1']].sum().to_numpy();rng=np.random.default_rng(SEED)
    s=g[rng.integers(0,len(g),(2000,len(g)))].sum(axis=1)
    imp=1-s[:,1]/s[:,0]
    return {'relative_WAPE_improvement':float(1-g[:,1].sum()/g[:,0].sum()), 'task_bootstrap_95CI':np.quantile(imp,[.025,.975]).tolist()}

def rebench():
    d=pd.read_json(DATA/'trajectories.metadata.jsonl',lines=True)
    assert not d.duplicated(['config','task','run']).any()
    assert len(d)==9435 and d.task.nunique()==111
    membership=d.task.map(split)
    train_repositories=set(d.loc[membership=='train','repository'])
    test_repositories=set(d.loc[membership=='test','repository'])
    (ROOT/'repository-overlap-audit.json').write_text(json.dumps({
        'train_repositories':len(train_repositories), 'test_repositories':len(test_repositories),
        'overlap':len(train_repositories&test_repositories),
        'note':'Task-heldout evaluation does not establish generalization to new repositories.'
    },indent=2),encoding='utf-8')
    audit={'rows':len(d),'tasks':int(d.task.nunique()),'configurations':int(d.config.nunique()),'resolved':int(d.resolved.sum()),
           'evaluation_mismatch':int((~d.evaluation_matches).sum()),'cost_missing':int(d.cost.isna().sum()),
           'token_total_missing':int(d.token_total.isna().sum()),'token_total_zero':int((d.token_total==0).sum()),
           'status':d.status.value_counts().to_dict(), 'exit_status':d.exit_status.value_counts().to_dict(),
           'per_config':d.groupby('config').agg(n=('task','size'),missing_cost=('cost',lambda x:int(x.isna().sum())),
             missing_tokens=('token_total',lambda x:int(x.isna().sum())),prefix_reached=('prefix_reached','sum')).reset_index().to_dict('records')}
    common=d[(d.kind=='common_scaffold_model')&d.evaluation_matches&(d.token_total>0)].copy()
    common['target']=common.token_total/1e6
    audit['common_valid']=len(common)
    audit['common_token_sum_match_fraction']=float(np.isclose(common.token_total,common.token_input+common.token_output).mean())
    # Matched repeat variation, not independent-rollout confidence intervals.
    groups=common.groupby(['task','config']).target.agg(['size','min','max','mean','std'])
    g5=groups[groups['size']==5].copy(); g5['max_min_ratio']=g5['max']/g5['min']
    variation={'five_run_cells':len(g5),'ratio_median':float(g5.max_min_ratio.median()),'ratio_p90':float(g5.max_min_ratio.quantile(.9)),
               'ratio_gt2_fraction':float((g5.max_min_ratio>2).mean()),'ratio_gt5_fraction':float((g5.max_min_ratio>5).mean()),
               'ratio_max':float(g5.max_min_ratio.max())}
    # Within each task/config, compare most/least consuming run. No causal stopping claim.
    low=common.loc[common.groupby(['task','config']).target.idxmin()]; high=common.loc[common.groupby(['task','config']).target.idxmax()]
    variation['min_usage_success_rate']=float(low.resolved.mean()); variation['max_usage_success_rate']=float(high.resolved.mean())
    ranks=common.groupby('config').target.rank(pct=True)
    variation['within_config_top10_usage_share']=float(common.loc[ranks>.9,'target'].sum()/common.target.sum())
    variation['failure_usage_share']=float(common.loc[~common.resolved,'target'].sum()/common.target.sum())
    g5.to_csv(ROOT/'repeat-variation.csv')
    cs=common.groupby('config').agg(n=('target','size'),score=('resolved','mean'),mean_million_tokens=('target','mean'),median_million_tokens=('target','median'),p90_million_tokens=('target',lambda x:x.quantile(.9))).reset_index()
    cs.to_csv(ROOT/'configuration-summary.csv',index=False)
    # Blind grouped test: only early scalar features.
    eligible=common[common.prefix_reached].copy();eligible['split']=eligible.task.map(split)
    parts={k:eligible[eligible['split']==k].copy().reset_index(drop=True) for k in ('train','validation','test')};tr=parts['train'];te=parts['test']
    assert all(not set(parts[a].task)&set(parts[b].task) for a,b in [('train','test'),('train','validation'),('validation','test')])
    means=tr.groupby('config').target.mean()
    baseline={k:x.config.map(means).to_numpy() for k,x in parts.items()}
    pred1,imp1=fit_predict(tr,parts,False);pred2,imp2=fit_predict(tr,parts,True)
    results={'population_rows':len(eligible),'population_tasks':eligible.task.nunique(),'excluded_before_tenth_result':len(common)-len(eligible),
             'splits':{k:{'rows':len(x),'tasks':x.task.nunique()} for k,x in parts.items()},
             'M0_configuration_mean':metric(te,baseline['test']),'M1_configuration_language':metric(te,pred1['test']),
             'M2_prefix':metric(te,pred2['test']),'M2_vs_M0':paired_bootstrap(te,baseline['test'],pred2['test']),
             'M2_vs_M1':paired_bootstrap(te,pred1['test'],pred2['test']),
             'feature_importance_impurity_diagnostic_not_causal':imp2}
    p90=tr.groupby('config').target.quantile(.9)
    vscores=pred2['validation']/parts['validation'].config.map(p90).to_numpy();cutoff=float(np.quantile(vscores,.9))
    scores=pred2['test']/te.config.map(p90).to_numpy(); alert=scores>=cutoff;highcost=te.target.to_numpy()>te.config.map(p90).to_numpy()
    risk={'validation_cutoff':cutoff,'alerts':int(alert.sum()),'alert_rate':float(alert.mean()),'high_usage_runs':int(highcost.sum()),
          'precision':float(highcost[alert].mean()) if alert.any() else None,
          'recall':float(alert[highcost].mean()) if highcost.any() else None,
          'usage_share_flagged':float(te.target[alert].sum()/te.target.sum()),
          'flagged_success_rate':float(te.resolved[alert].mean()),
          'overall_success_rate':float(te.resolved.mean()),'remaining_usage_or_savings':None}
    results['risk_alert']=risk
    out=te[['task','config','language','run','target','resolved']].copy()
    out['M0']=baseline['test'];out['M1']=pred1['test'];out['M2']=pred2['test'];out['alert']=alert;out['high_usage']=highcost
    out.to_csv(ROOT/'heldout-predictions.csv',index=False)
    by=[]
    for cfg,ix in te.groupby('config').groups.items():
        sub=te.loc[ix];by.append({'config':cfg,'M0_WAPE':metric(sub,baseline['test'][ix])['WAPE'],'M1_WAPE':metric(sub,pred1['test'][ix])['WAPE'],'M2_WAPE':metric(sub,pred2['test'][ix])['WAPE']})
    pd.DataFrame(by).to_csv(ROOT/'heldout-by-configuration.csv',index=False)
    # Figures use English labels to avoid unavailable CJK fonts; report text is Chinese.
    fig,axes=plt.subplots(1,3,figsize=(15,4.4),layout='constrained')
    axes[0].hist(g5.max_min_ratio.clip(upper=10),bins=np.linspace(1,10,28),color='#3768b2');axes[0].set(xlabel='Max / min tokens (same task + configuration)',ylabel='Task-configuration cells',title='Five repeated runs; values >10 clipped')
    axes[1].bar(['Config mean','Config + language','Early prefix'],[results[k]['WAPE']*100 for k in ('M0_configuration_mean','M1_configuration_language','M2_prefix')],color=['#8a9bb0','#5d8bca','#217a67']);axes[1].set(ylabel='Heldout WAPE (%)',title=f"Grouped test: {te.task.nunique()} unseen tasks")
    axes[2].scatter(cs.mean_million_tokens,cs.score*100,color='#3768b2');
    for _,r in cs.iterrows(): axes[2].annotate(r['config'],(r.mean_million_tokens,r.score*100),fontsize=6,xytext=(2,2),textcoords='offset points')
    axes[2].set(xlabel='Mean reported tokens (millions)',ylabel='Benchmark resolved (%)',title='Common scaffold; tokens are not USD')
    fig.savefig(ROOT/'findings.svg');plt.close(fig)
    return {'audit':audit,'variation':variation,'forecast':results,'configurations':cs.to_dict('records')}

def rightfit():
    d=pd.read_csv(DATA/'sources/right_fit/results/task_level.tsv',sep='\t')
    assert len(d)==6204 and not d.duplicated(['benchmark','harness','model','task_id']).any()
    audit={'rows':len(d),'configurations':d.groupby(['benchmark','harness','model']).ngroups,'cost_missing':int(d.cost_usd.isna().sum()),
           'no_trajectory':int((~d.trajectory_available).sum()),'unresolved_zero':int((d.reward_status=='unresolved_zero').sum()),
           'nonbinary_rewards':int((~d.reward.isin([0,1])).sum()),'cached_exceeds_input':int((d.cached_tokens>d.input_tokens).sum()),
           'negative_cost':int((d.cost_usd<0).sum()),'zero_cost':int((d.cost_usd==0).sum())}
    comparisons=[];rng=np.random.default_rng(SEED)
    from itertools import combinations
    for (bench,model),g in d.groupby(['benchmark','model']):
        for a,b in combinations(sorted(g.harness.unique()),2):
            z=g[g.harness==a].merge(g[g.harness==b],on='task_id',suffixes=('_a','_b'))
            total=len(z); z=z[z.cost_usd_a.notna()&z.cost_usd_b.notna()]
            if not len(z):continue
            reward_delta=(z.reward_a-z.reward_b).to_numpy(); ca=z.cost_usd_a.to_numpy();cb=z.cost_usd_b.to_numpy()
            ix=rng.integers(0,len(z),(2000,len(z)))
            comparisons.append({'benchmark':bench,'model':model,'a':a,'b':b,'common_tasks':len(z),'excluded_missing_cost':total-len(z),
              'cost_a':float(ca.mean()),'cost_b':float(cb.mean()),'cost_ratio_a_b':float(ca.sum()/cb.sum()),
              'cost_ratio_95CI':np.quantile(ca[ix].sum(axis=1)/cb[ix].sum(axis=1),[.025,.975]).tolist(),
              'score_a':float(z.reward_a.mean()),'score_b':float(z.reward_b.mean()),'score_delta_a_b':float(reward_delta.mean()),
              'paired_score_delta_95CI':np.quantile(reward_delta[ix].mean(axis=1),[.025,.975]).tolist(),
              'cost_per_reward_equivalent_a':float(ca.sum()/z.reward_a.sum()) if z.reward_a.sum() else None,
              'cost_per_reward_equivalent_b':float(cb.sum()/z.reward_b.sum()) if z.reward_b.sum() else None})
    # All comparisons are exploratory, no multiple-testing corrected ranking claim.
    pd.DataFrame(comparisons).to_csv(ROOT/'harness-paired-comparisons.csv',index=False)
    summary=d.groupby(['benchmark','harness','model']).agg(rows=('task_id','size'),missing_cost=('cost_usd',lambda x:x.isna().sum()),mean_reward=('reward','mean'),mean_observed_cost=('cost_usd','mean')).reset_index()
    summary.to_csv(ROOT/'right-fit-summary.csv',index=False)
    return {'audit':audit,'paired_comparisons':comparisons}

if __name__=='__main__':
    ROOT.mkdir(parents=True,exist_ok=True)
    print('Analyzing Right Fit descriptive scalar results',flush=True);rf=rightfit()
    print('Analyzing SWE-rebench repeated runs and frozen heldout protocol',flush=True);sw=rebench()
    result={'swe_rebench':sw,'right_fit':rf,'seed':SEED}
    (ROOT/'results.json').write_text(json.dumps(result,indent=2,ensure_ascii=False))
    print(json.dumps({'swe_audit':sw['audit'],'variation':sw['variation'],'forecast':sw['forecast'],'right_fit_audit':rf['audit']},indent=2),flush=True)
