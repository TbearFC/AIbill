"""Offline paired cost/quality comparison. Stdlib only; never reads agent logs.

CSV/TSV columns: benchmark, model, harness (or config), task_id,
cost_usd, reward (or quality_score); optional run_id for repeated measurements.
Cost must be finite and nonnegative; unknown cost is never replaced with zero.
"""
import argparse, csv, json, math, os, random, statistics
from pathlib import Path

def quantile(values, q):
    a=sorted(values); p=(len(a)-1)*q; i=int(p)
    return a[i]+(a[min(i+1,len(a)-1)]-a[i])*(p-i)

def compare(rows, benchmark, model, baseline, candidate, margin=.05, draws=2000, seed=1706):
    if baseline==candidate: raise ValueError('Configurations must differ')
    if not 0<=margin<=1: raise ValueError('Quality margin must lie in [0,1]')
    selected={baseline:{},candidate:{}}; seen=set(); missing=0
    for row in rows:
        cfg=row.get('harness',row.get('config'))
        if row['benchmark']!=benchmark or row['model']!=model or cfg not in selected:continue
        key=(cfg,row['task_id'],row.get('run_id','single'))
        if key in seen:raise ValueError('Duplicate task/config/run; provide unique run_id for repeats')
        seen.add(key)
        try: cost=float(row['cost_usd']); score=float(row.get('reward',row.get('quality_score')))
        except (ValueError,TypeError):missing+=1;continue
        if not math.isfinite(cost) or not math.isfinite(score):missing+=1;continue
        if cost<0 or not 0<=score<=1:raise ValueError('Invalid cost or quality score')
        selected[cfg].setdefault(row['task_id'],[]).append((cost,score))
    if missing:raise ValueError(f'{missing} missing/invalid cost or score records in selected configurations; comparison refused')
    a,b=selected[baseline],selected[candidate];tasks=sorted(set(a)&set(b))
    if len(tasks)<8:raise ValueError('At least 8 paired tasks required; small samples still have wide intervals')
    if any(len(a[t])!=len(b[t]) for t in tasks):raise ValueError('Unequal repeated-run coverage on paired tasks')
    data=[(statistics.mean(v[0] for v in a[t]),statistics.mean(v[0] for v in b[t]),
           statistics.mean(v[1] for v in a[t]),statistics.mean(v[1] for v in b[t])) for t in tasks]
    ca,cb,sa,sb=map(statistics.mean,zip(*data))
    if ca<=0:raise ValueError('Zero baseline total cost: ratio undefined')
    rng=random.Random(seed); ratios=[]; deltas=[]
    for _ in range(draws):
        sample=rng.choices(data,k=len(data));x,y,u,v=map(statistics.mean,zip(*sample))
        if x>0:ratios.append(y/x)
        deltas.append(v-u)
    ratio_ci=[quantile(ratios,.025),quantile(ratios,.975)];delta_ci=[quantile(deltas,.025),quantile(deltas,.975)]
    quality_supported=delta_ci[0]>=-margin
    status=('lower_cost_with_quality_margin_supported' if ratio_ci[1]<1 and quality_supported else
            'cost_increase_supported' if ratio_ci[0]>1 else 'insufficient_evidence')
    if delta_ci[1]<-margin:status='quality_regression_supported'
    return {'benchmark':benchmark,'model':model,'baseline':baseline,'candidate':candidate,'paired_tasks':len(tasks),
            'runs_per_config':sum(len(a[t]) for t in tasks),'unmatched_baseline_tasks':len(set(a)-set(b)),
            'unmatched_candidate_tasks':len(set(b)-set(a)),'mean_cost_baseline':ca,'mean_cost_candidate':cb,
            'candidate_baseline_cost_ratio':cb/ca,'cost_ratio_task_bootstrap_95CI':ratio_ci,
            'mean_quality_baseline':sa,'mean_quality_candidate':sb,'quality_difference_candidate_baseline':sb-sa,
            'quality_difference_task_bootstrap_95CI':delta_ci,'quality_margin':margin,
            'quality_margin_supported':quality_supported,'sample_decision':status,
            'method':'Equal weight per paired task; aggregate repeats before task bootstrap. Single comparison, no multiplicity correction.',
            'limit':'Historical observed comparison; not a guarantee of production savings. Predeclare a candidate and margin before prospective testing.'}

def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data',type=Path,required=True)
    for key in ('benchmark','model','baseline','candidate'):p.add_argument('--'+key,required=True)
    p.add_argument('--quality-margin',type=float,default=.05);p.add_argument('--output',type=Path)
    args=p.parse_args(); delimiter='\t' if args.data.suffix=='.tsv' else ','
    with args.data.open(newline='',encoding='utf-8-sig') as f:rows=list(csv.DictReader(f,delimiter=delimiter))
    try:r=compare(rows,args.benchmark,args.model,args.baseline,args.candidate,args.quality_margin)
    except ValueError as e:p.error(str(e))
    text=json.dumps(r,indent=2,ensure_ascii=False)+'\n'
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(text,encoding='utf-8')
    print(text,end='')

if __name__=='__main__':main()
