import concurrent.futures, gzip, hashlib, json, os, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent / 'data'
os.umask(0o077)
REV = 'cdae27cdd16673f0c682871ad55d325f24cc7020'
BASE = f'https://huggingface.co/datasets/ibragim-bad/swe_rebench_07_2026_trajectories/resolve/{REV}/'


def download(meta):
    relative=Path(meta['path'])
    base=ROOT/'sources/swe_rebench'
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Invalid trajectory destination')
    p = base/relative
    if not p.resolve().is_relative_to(base.resolve()):
        raise ValueError('Trajectory destination escapes data directory')
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        for attempt in range(4):
            try:
                with urllib.request.urlopen(BASE+meta['path'],timeout=90) as r, open(str(p)+'.part','wb') as w:
                    while b := r.read(1024*1024): w.write(b)
                Path(str(p)+'.part').replace(p); break
            except Exception:
                if attempt == 3: raise
                time.sleep(2**attempt)
    rawhash=hashlib.sha256(); rows=[]
    with gzip.open(p,'rb') as f:
        for line in f:
            rawhash.update(line); rows.append(extract(json.loads(line)))
    assert len(rows)==meta['rows'], (str(p),'row mismatch')
    assert rawhash.hexdigest()==meta['content_sha256'], (str(p),'uncompressed hash mismatch')
    return rows, {'path':str(p.relative_to(ROOT)), 'revision':REV, 'url':BASE+meta['path'], 'bytes':p.stat().st_size, 'sha256':hashlib.sha256(p.read_bytes()).hexdigest(), 'uncompressed_sha256':rawhash.hexdigest(),'rows':len(rows)}

def chars(v):
    if isinstance(v,str): return len(v)
    if v is None: return 0
    return len(json.dumps(v,ensure_ascii=False,sort_keys=True))

def extract(x):
    # Export scalar metadata only: no prompts, task prose, file paths or tool arguments.
    u=x['usage']; tok=u.get('tokens') or {}; ev=x['evaluation']; part=x['participant']
    row={'id':x['trajectory_id'],'task':x['instance_id'],'repository':x['repository'],'language':x['language'],
         'config':part['key'],'kind':part['type'],'scaffold':part['scaffold'],'model':part['model'],
         'run':x['run']['index'],'retry':x['run'].get('retry_number'), 'cost':u.get('cost_usd'),
         'steps':u.get('steps'),'model_calls':u.get('model_calls'),'tool_calls':u.get('tool_calls'),
         'seconds':u.get('wall_time_seconds'),'resolved':bool(ev['resolved']),'status':ev['status'],
         'exit_status':ev.get('agent_exit_status'),'evaluation_matches':ev.get('evaluation_matches_selected'),
         'token_total':tok.get('total'),'token_input':tok.get('input'),'token_cached':tok.get('cached_input'),
         'token_output':tok.get('output'),'token_cache_creation':tok.get('cache_creation')}
    events=x['events']; prefix=[]; n=0; reached=False
    for e in events:
        prefix.append(e)
        if e['type']=='tool_result':
            n+=1
            if n==10: reached=True; break
    row['prefix_reached']=reached
    if reached:
        calls=[e for e in prefix if e['type']=='tool_call']; results=[e for e in prefix if e['type']=='tool_result']
        tools=[e.get('tool',e.get('name','unknown')) for e in calls]
        inputs=[json.dumps(e.get('input'),sort_keys=True,ensure_ascii=False) for e in calls]
        row.update({'p_tool_diversity':len(set(tools)), 'p_tool_repeats':len(tools)-len(set(tools)),
                    'p_input_repeats':len(inputs)-len(set(inputs)), 'p_errors':sum(e.get('status')=='error' or (isinstance(e.get('exit_code'),int) and e['exit_code']!=0) for e in results),
                    'p_result_chars':sum(chars(e.get('content')) for e in results),
                    'p_input_chars':sum(chars(e.get('input')) for e in calls),
                    'p_assistant_chars':sum(chars(e.get('content')) for e in prefix if e['type'] in ('assistant_message','assistant_analysis')),
                    'p_truncated':sum(bool(e.get('truncated')) or e.get('omitted_reason') is not None for e in results),
                    'p_calls':len(calls), 'p_events':len(prefix)})
    return row

if __name__=='__main__':
    from fetch_sources import fetch_sources
    fetch_sources()
    INDEX = [json.loads(s) for s in (ROOT/'sources/swe_rebench/index.jsonl').read_text().splitlines()]
    rows=[]; manifest=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futs={pool.submit(download,m):m for m in INDEX}
        for i,f in enumerate(concurrent.futures.as_completed(futs),1):
            a,b=f.result(); rows.extend(a); manifest.append(b)
            if i%10==0 or i==len(INDEX): print(f'validated {i}/{len(INDEX)} shards; {len(rows)} trajectories',flush=True)
    rows.sort(key=lambda r:(r['config'],r['run'],r['task']))
    (ROOT/'trajectories.metadata.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
    (ROOT/'trajectory-manifest.json').write_text(json.dumps(sorted(manifest,key=lambda m:m['path']),indent=2))
    print('DONE scalar metadata only',len(rows),flush=True)
