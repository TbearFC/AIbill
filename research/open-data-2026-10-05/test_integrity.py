import copy, unittest
import collect
from analyze import features,split
from compare_configs import compare
import pandas as pd

class Integrity(unittest.TestCase):
    def fixture(self):
        return {'trajectory_id':'t','instance_id':'task','repository':'public/repo','language':'python',
                'participant':{'key':'fixture','type':'common_scaffold_model','scaffold':'fixture','model':'fixture'},
                'run':{'index':0},'usage':{'cost_usd':10,'tokens':{'total':100}},
                'evaluation':{'resolved':False,'status':'failed','evaluation_matches_selected':True},
                'events':sum(([{'type':'tool_call','tool':'shell','input':{'command':str(i)}},
                              {'type':'tool_result','status':'success','content':'public fixture'}] for i in range(10)),[])}
    def test_future_suffix_invariance(self):
        x=self.fixture();y=copy.deepcopy(x);y['events'] += [{'type':'tool_result','status':'error','content':'FUTURE'*10000}]
        p=lambda z:{k:v for k,v in collect.extract(z).items() if k.startswith('p_')}
        self.assertEqual(p(x),p(y))
    def test_target_outcome_not_features(self):
        x=collect.extract(self.fixture()); y=copy.deepcopy(x)
        y.update(cost=999999,token_total=999999,resolved=True,steps=99999,status='resolved',seconds=99999)
        self.assertEqual(features(pd.DataFrame([x]),True),features(pd.DataFrame([y]),True))
    def test_no_sensitive_text_exported(self):
        x=self.fixture();x['events'][0]['input']={'command':'PRIVATE_MARKER'}
        self.assertNotIn('PRIVATE_MARKER',str(collect.extract(x)))
    def test_tenth_result_required(self):
        x=self.fixture();x['events']=x['events'][:-1]
        self.assertFalse(collect.extract(x)['prefix_reached'])
    def test_split_same_task_all_runs(self):
        self.assertTrue(all(split('task')==split('task') for _ in range(20)))
    def rows(self):
        return [{'benchmark':'b','model':'m','harness':cfg,'task_id':str(t),'cost_usd':cost,'reward':score}
                for t in range(12) for cfg,cost,score in [('old',2,1),('new',1,1)]]
    def test_known_improvement(self):
        r=compare(self.rows(),'b','m','old','new',draws=200)
        self.assertEqual(r['candidate_baseline_cost_ratio'],.5)
        self.assertEqual(r['sample_decision'],'lower_cost_with_quality_margin_supported')
    def test_missing_never_zero(self):
        r=self.rows();r[0]['cost_usd']=''
        with self.assertRaises(ValueError):compare(r,'b','m','old','new')
    def test_duplicate_rejected(self):
        r=self.rows();r.append(r[0])
        with self.assertRaises(ValueError):compare(r,'b','m','old','new')
    def test_quality_loss_blocks_cost_win(self):
        r=self.rows()
        for row in r:
            if row['harness']=='new':row['reward']=0
        self.assertEqual(compare(r,'b','m','old','new',draws=200)['sample_decision'],'quality_regression_supported')
    def test_no_pairing_by_row_order(self):
        r=self.rows(); a=compare(r,'b','m','old','new',draws=200)
        self.assertEqual(a,compare(r[::-1],'b','m','old','new',draws=200))

if __name__=='__main__':unittest.main()
