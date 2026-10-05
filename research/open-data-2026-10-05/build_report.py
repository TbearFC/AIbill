import html,json,os
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT=Path(__file__).resolve().parent / 'outputs'
os.umask(0o077)
def load(name):return json.loads((ROOT/name).read_text())
def pct(x):return f'{100*x:.1f}%'
def main():
    r=load('results.json');s=r['swe_rebench'];f=s['forecast'];v=s['variation'];a=s['audit'];b=r['right_fit']['audit']
    good=load('configuration-regression-example.json');bad=load('quality-regression-example.json')
    transfer=load('cost-aware-transfer-diagnostic.json');repci=load('repeat-variation-ci.json');repos=load('repository-overlap-audit.json')
    # Two counterexamples prevent a universal "use PI" interpretation.
    fig,axes=plt.subplots(1,2,figsize=(10,4.5),layout='constrained')
    for ax,x,title in zip(axes,[good,bad],['GPT-6 Astra / ALE-CLI (99 paired tasks)','Claude Opus 5 / Terminal-Bench 4 (63 tasks)']):
        ax.bar([x['baseline'],x['candidate']],[x['mean_cost_baseline'],x['mean_cost_candidate']],color=['#7893b0','#217a67'])
        ax.set(title=title,ylabel='Historical reported USD / task')
        for i,(cost,score) in enumerate([(x['mean_cost_baseline'],x['mean_quality_baseline']),(x['mean_cost_candidate'],x['mean_quality_candidate'])]):
            ax.text(i,cost,f'${cost:.2f}\nscore {score*100:.1f}%',ha='center',va='bottom',fontsize=10)
        ax.set_ylim(0,max(x['mean_cost_baseline'],x['mean_cost_candidate'])*1.22)
    fig.savefig(ROOT/'paired-cost-quality.svg');plt.close(fig)
    forecast_rows=''.join(f'<tr><td>{label}</td><td>{pct(f[key]["WAPE"])}</td><td>{pct(f[key]["macro_WAPE"])}</td></tr>' for label,key in [('配置历史均值','M0_configuration_mean'),('配置 + 语言','M1_configuration_language'),('配置 + 语言 + 前缀','M2_prefix')])
    audit_rows=''.join(f'<tr><td>{html.escape(x["config"])}</td><td>{x["n"]}</td><td>{x["missing_cost"]}</td><td>{x["missing_tokens"]}</td><td>{x["prefix_reached"]}</td></tr>' for x in a['per_config'])
    report=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AIbill 开放数据实证研究 · 第一轮</title>
<style>body{{margin:0;background:#f1f4f8;color:#172b42;font:16px/1.8 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}main{{max-width:1120px;margin:auto;padding:40px 24px 80px}}section{{background:white;margin:22px 0;padding:26px 30px;border-radius:14px}}h1{{font-size:34px;line-height:1.35}}h2{{font-size:24px;margin-top:0}}h3{{font-size:18px}}p{{margin:12px 0}}a{{color:#225faa}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:10px;text-align:left;border-bottom:1px solid #dce4ed}}th{{background:#edf3f8}}img{{width:100%;height:auto}}code,pre{{font:13px/1.6 ui-monospace,monospace}}pre{{white-space:pre-wrap;background:#edf3f8;padding:18px;border-radius:8px}}.tag{{color:#557189;font-size:13px}}.lead{{font-size:20px}}.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}}.card{{padding:18px;background:#edf3f8;border-radius:10px}}.number{{font-size:27px;font-weight:700}}.note{{color:#526879;font-size:14px}}.warning{{border-left:4px solid #a56d1a;padding-left:16px}}@media(max-width:750px){{.cards{{grid-template-columns:1fr}}section{{padding:20px 14px}}table{{font-size:12px}}}}</style>
<main><div class="tag">2026-10-05 · 独立离线分析 · 来源固定版本 · 公开数据与个人日志隔离</div>
<h1>AIbill 的第一项研究能力：<br>按任务比较配置的成本与质量</h1>
<p class="lead">本轮证据支持优先开发<strong>配置成本与质量回归检查</strong>。它回答：在自己的任务上，换模型、执行框架或配置后，是否花得更多，交付是否退步？简单前缀预测未通过本轮检验，尚不支持上线自动中止或宣称精准预算预测。</p>
<div class="cards"><div class="card"><div class="number">15,639 条</div>两组公开运行记录，来源分别校验</div><div class="card"><div class="number">2.19 倍</div>同任务同配置，5 次运行的最大/最小 Token 比值中位数</div><div class="card"><div class="number">60.3% ↓</div>一个固定模型、99 个配对任务中，更换框架的报告成本差异</div></div>
<section><h2>1. 关键突破口与证据等级</h2>
<table><tr><th>候选方向</th><th>本轮证据</th><th>当前判断</th></tr>
<tr><td>配置选择与升级回归检查</td><td>相同模型与任务下，框架的成本和质量差异明显；另一个配对例子同时更贵且更差。</td><td>可以做可复现的离线比较 CLI；再接用户自己的验收任务。</td></tr>
<tr><td>执行早期精准预测</td><td>前 10 次工具反馈的简单结构特征，未降低留出任务误差。</td><td>本方案暂不进入自动控制。保留负结果，不泛化为所有前缀算法都无效。</td></tr>
<tr><td>积累全用户日志带来预测壁垒</td><td>公开数据是模型配置与基准任务，不是用户行为与每周需求。</td><td>本轮无法验证多用户周预测增益或商业护城河。</td></tr></table>
<p>这是产品切入方向的证据，不是学术原创性声明。<a href="https://arxiv.org/abs/2610.00917">Finding the Right Fit</a>已研究模型与执行框架的交互；<a href="https://arxiv.org/abs/2604.22750">Agent Token Consumption</a>已研究执行消耗与预测；<a href="https://arxiv.org/abs/2609.31995">CER</a>已研究用语义前缀预测终局奖励。本轮增加了独立数据核对、任务配对区间、冻结的简单预测检验及可运行比较工具。</p></section>
<section><h2>2. 相同模型与任务，更换框架有什么影响</h2>
<img src="paired-cost-quality.svg" alt="两个相反的框架配对例子：同框架不具有普遍优势">
<table><tr><th>配对例子</th><th>报告成本 / 任务</th><th>平均评测分数</th><th>按任务 Bootstrap 95% 区间</th></tr>
<tr><td>GPT-6 Astra · ALE-CLI · DSH → PI<br>99 个相同任务；无缺失成本</td><td>${good['mean_cost_baseline']:.2f} → ${good['mean_cost_candidate']:.2f}<br>成本比 {good['candidate_baseline_cost_ratio']:.3f}</td><td>{pct(good['mean_quality_baseline'])} → {pct(good['mean_quality_candidate'])}<br>+9.43 个百分点</td><td>成本比 0.322–0.503<br>分数差 +2.93 至 +16.40 个百分点</td></tr>
<tr><td>Claude Opus 5 · Terminal-Bench 4 · OpenHands → PI<br>63 个相同任务；无缺失成本</td><td>${bad['mean_cost_baseline']:.2f} → ${bad['mean_cost_candidate']:.2f}<br>成本比 {bad['candidate_baseline_cost_ratio']:.3f}</td><td>{pct(bad['mean_quality_baseline'])} → {pct(bad['mean_quality_candidate'])}<br>−26.98 个百分点</td><td>成本比 1.150–2.183<br>分数差 −41.27 至 −14.29 个百分点</td></tr></table>
<p>工具保留失败任务的消耗，并把重复运行先按任务平均，再对任务整体抽样。ALE-CLI 有部分得分，平均分数不是成功率；Terminal-Bench 4 在此数据中是二元得分。这里的美元是来源按 OpenRouter 价格核算的历史 Agent 模型成本，未覆盖人工、所有工具、容器和订阅支出，也不是经过账单审计的现金支出。</p>
<p class="warning">这两组例子是在全数据分析后选择的探索性例子，区间没有做多重比较校正。它们复现既有研究的方向，不能直接承诺用户上线后节省 60.3%，也不能证明某框架普遍更好。</p></section>
<section><h2>3. 继续挖掘：跨任务推荐为什么会失效</h2>
<p>在四种可配置框架、五种模型和三类任务上，使用其中两类任务选配置，再观察第三类任务。规则为：平均得分距最优不超过 5 个百分点时，选平均相对成本最低者。相对成本先按任务域归一化，两类任务域等权。</p>
<p><strong>15 个组合中有 5 个在第三类任务上的得分损失超过 5 个百分点。</strong>例如 Claude Opus 5 在另外两类任务上会选出 PI，转到 Terminal-Bench 4 后，相比 OpenHands 成本高 56.8%，分数低 26.98 个百分点。</p>
<p>控制检查也值得保留：如果只按平均质量选择框架，跨域相对最高分的平均损失仅为 {100*transfer['quality_only_control']['mean_quality_gap']:.2f} 个百分点，最大 {100*transfer['quality_only_control']['maximum_quality_gap']:.2f} 个百分点。<strong>风险具体来自这次带成本约束的选择规则，不能夸大成所有跨域推荐都不可靠。</strong></p>
<p class="note">这是观察全数据后的探索性诊断，只有 3 个任务域；5 个百分点是示例规则，不是已验证的用户容忍度。参考最优配置用到了留出域的结果，只能作事后参照。详细记录：<a href="cost-aware-transfer-diagnostic.json">迁移诊断</a>。</p></section>
<section><h2>4. 成本波动与早期预测的负结果</h2>
<img src="findings.svg" alt="重复运行的Token波动、任务留出预测误差、配置消耗与成功分布">
<p>统一框架的 13 种配置产生 7,215 条有效运行。对 1,443 个任务与配置组合，每组均有 5 次运行，最大 / 最小 Token 比值中位数为 {v['ratio_median']:.2f}，任务聚类 Bootstrap 95% 区间为 {repci['median_ratio_task_bootstrap_95CI'][0]:.2f}–{repci['median_ratio_task_bootstrap_95CI'][1]:.2f}；{pct(v['ratio_gt2_fraction'])} 的组合超过 2 倍。每种配置内最昂贵的约 10% 运行，占合计 Token 的 {pct(v['within_config_top10_usage_share'])}。</p>
<p>失败轨迹占总 Token 的 {pct(v['failure_usage_share'])}。这表示必须把失败成本计入单位任务成本；它不表示这些 Token 全是可避免浪费。昂贵运行也有成功案例，不能从事后失败推断当时应被中止。</p>
<table><tr><th>冻结方案</th><th>测试 WAPE</th><th>每配置宏平均 WAPE</th></tr>{forecast_rows}</table>
<p>以任务 ID 的 SHA256 划分：训练 73 个任务，验证 19 个任务，最终测试 19 个任务 / 1,233 条运行。同任务的所有配置和重复运行都留在同一分区。特征只截取第 10 个工具结果以前的工具多样性、重复输入、错误、字符数和截断信号。终局用量、步数、时长、退出状态、成功标签均禁止作为特征。测试集的 19 个仓库中有 {repos['overlap']} 个出现在训练仓库中，尚未验证全新仓库泛化。</p>
<p>加入前缀后，相比配置历史均值的相对误差改善为 −1.30%，任务 Bootstrap 95% 区间为 −11.26% 至 +8.11%；没有可确认改善。高消耗定义使用训练集每配置 P90，报警阈值固定为验证集风险分数 P90。测试实际报警 {pct(f['risk_alert']['alert_rate'])}，命中率 {pct(f['risk_alert']['precision'])}，召回率 {pct(f['risk_alert']['recall'])}；实际高消耗比例 {pct(f['risk_alert']['high_usage_runs']/f['risk_alert']['alerts']*f['risk_alert']['alert_rate'])}。预测目标是整次 Token，不是剩余成本。</p>
<p class="note">固定 300 棵随机森林、深度 8、叶节点至少 15 条，训练内 Duan 校正；没有通过反复看测试集调参数。达到 10 个工具结果的运行才进入预测研究，排除 89 条较短轨迹。原计划预测 USD，在发现统一框架记录缺失 USD 后、拟合前改为 Token；<a href="../protocol.json">原协议</a>与<a href="../protocol-amendment.json">修订记录</a>均保留。不能据此判断更复杂语义信号或更晚的用量信号没有价值。</p></section>
<section><h2>5. 云成本与 Agent 历史：能落地的经验</h2>
<p><a href="https://aws.amazon.com/blogs/aws-cloud-financial-management/understand-and-build-driver-based-forecasting/">AWS 的需求驱动预测</a>强调业务需求与资源消耗的关系；<a href="https://www.finops.org/framework/capabilities/unit-economics/">FinOps 单位经济性</a>把成本连接到业务产出。迁移到 Agent，应按任务量、配置和验收结果建立成本分布，再估算下一批任务的预算。消耗日志本身不能告诉我们用户下周新增多少工作。</p>
<p><a href="https://arxiv.org/abs/2210.03629">ReAct（2022）</a>展示推理与行动交替的轨迹；<a href="https://arxiv.org/abs/2405.15793">SWE-agent（2024）</a>研究运行接口对软件工程效果的影响；<a href="https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents">长时间运行 Agent 的框架实践（2025）</a>进一步关注跨上下文进度。数据单位应从单次调用扩展到任务、重试和交付，记录模型与框架版本。</p>
<p><a href="https://langfuse.com/docs/observability/features/token-and-cost-tracking">Langfuse</a>已提供用量、成本追踪和告警。AIbill 可以优先研究一个更具体的用户决策：<strong>我的任务用哪种配置划算，这次升级会不会花得更多、交付退步？</strong>这仍需后续验证需求、使用频率和付费意愿，不能凭公开基准确认市场壁垒。</p></section>
<section><h2>6. 已完成的第一步：离线成本与质量比较 CLI</h2>
<p><a href="../compare_configs.py">compare_configs.py</a>只依赖 Python 标准库，无网络请求，不读取个人 Agent 日志。接受用户指定 CSV/TSV，按任务 ID 配对，支持 run_id 标记的重复运行，输出成本比、质量差、任务 Bootstrap 区间和当前样本判断。缺失成本、重复记录、覆盖不等及质量明显退步会被识别。当前已用上面两组真实公开结果验证；完整性测试使用人工构造数据，运行方式见本目录 README。</p>
<pre>python compare_configs.py \\
  --data data/sources/right_fit/results/task_level.tsv \\
  --benchmark ALE-CLI --model 'GPT-6 Astra' \\
  --baseline DSH --candidate PI \\
  --quality-margin 0.05 --output outputs/comparison.json</pre>
<p><strong>用户侧下一步：</strong>固定 20 个可验收的真实任务、一个当前配置与一个候选配置；预先写明验收条件、可接受质量损失和统一价格口径，两种配置各运行 3 次。保留独立的后续任务作验证，报告包含失败成本和不确定区间。20 个任务是起始样本，不保证达到统计把握；结果不确定时显示“证据不足”。本轮未调用付费模型，也未执行这些未来任务。</p>
<p>看板第一屏建议包含：任务批次与配置版本选择、成本来源和覆盖率、成本比与质量差区间、证据不足状态、单任务差异下钻。人工验收与基准分数需要分别标记。路径由用户本机配置，研究记录仅保留匿名任务 ID 和必要数值字段。</p>
<p>模板：<a href="../task-record-template.csv">用户批次记录表头</a>；真实公开数据输出：<a href="configuration-regression-example.json">低成本例子</a>、<a href="quality-regression-example.json">质量退步例子</a>。</p></section>
<section><h2>7. 数据核对与可复现材料</h2>
<p><a href="https://huggingface.co/datasets/ibragim-bad/swe_rebench_07_2026_trajectories/tree/cdae27cdd16673f0c682871ad55d325f24cc7020">SWE-rebench 固定版本</a>：111 任务 × 17 配置 × 5 次，共 9,435 条；85 个压缩分片，逐个验证来源给出的未压缩 SHA256，另保存压缩文件 SHA256。没有重复任务 / 配置 / run；没有 evaluation_matches_selected 不匹配。6 条无 Token，8,331 条无 USD。13 种统一框架配置全部缺 USD，不能补零。统一框架总 Token 均等于 input + output，cached 为输入子集，不再次相加；原生系统口径另行保留。</p>
<details><summary>展开各配置覆盖</summary><table><tr><th>配置</th><th>行数</th><th>USD 缺失</th><th>Token 缺失</th><th>达到前缀</th></tr>{audit_rows}</table></details>
<p><a href="https://huggingface.co/datasets/yixuanli97/finding-the-right-fit/tree/92ffbd7daf5ce7bc4b1e996e962a46ca52876820">Right Fit 固定版本</a>：6,204 条、66 配置；无重复任务 / 配置，118 条成本缺失，其中 Codex / TUA-Bench 为 117 条，因此不作该配置的完整成本排名。137 条没有轨迹、1,078 条非二元奖励、119 条已报告零成本（均无正 Token），没有负成本或 cached 超过 input。四个可配置框架共 5,640 条，成本字段完整。缺失与已记录零值严格区分。</p>
<p>公开可下载不等于无条件使用。SWE-rebench 保留原仓库及模型提供方条款；Right Fit 标为 CC BY-NC 4.0，要求仅用于分析研究，不用于模型训练、微调或蒸馏。本轮对 Right Fit 只做数值统计，没有用它拟合预测器。原始内容保留在本地，不写入公开代码库。</p>
<pre>python fetch_sources.py           # 公开元数据 / 数值表，固定版本 + 哈希验证
python collect.py                 # 轨迹分片下载 + 哈希验证
python analyze.py                 # 主分析、冻结预测与图表
python dig_deeper.py              # 单独标记的探索性迁移诊断
python -m unittest -v test_integrity
python build_report.py</pre>
<p>文件清单：<a href="../source-manifest.json">来源与哈希</a>、85 分片哈希在本地 data/trajectory-manifest.json 中生成、<a href="results.json">完整指标</a>、逐行留出预测仅在本地生成、<a href="harness-paired-comparisons.csv">框架配对比较</a>、逐任务重复运行波动仅在本地生成、<a href="../requirements-research.txt">研究依赖</a>。源数据为本地快照，模型名按来源原样作配置标签，不代表当前官方产品推荐。本报告只包含公开来源的研究汇总，不包含个人日志。</p></section>
<section><h2>8. 下一轮最值得验证什么</h2>
<ol><li><strong>在自己工作负载上复现配对优势。</strong>先用离线比较，再前瞻验证同一批真实任务的实际支出与人工验收，检查差异是否稳定。单用户也可开始。</li>
<li><strong>补逐调用用量与显式验收进度。</strong>再研究当前已花成本、上下文增量、重复工具输出、复现与测试里程碑是否改善“剩余成本 + 完成概率”；字符长度只能作代理。继续分开 Token、估算 USD 与账单真值。</li>
<li><strong>验证跨配置或跨用户数据的增益。</strong>固定目标样本规模，用相同任务与配置覆盖比较增加外部样本前后的误差；按任务、仓库和时间留出，并检验版本变化。当前没有用户周数据，不能给出多用户增益曲线。</li>
<li><strong>最后才检验干预收益。</strong>高成本预警先仅展示；通过受控继续 / 暂停 / 切换实验记录节省与丢失的成功任务，计入重启和切换成本。离线事后数据无法识别中止后的反事实。</li></ol>
<p class="note">本文件记录已完成的一轮分析与后续验证顺序；尚未安排定时运行、产品部署或用户招募。</p></section></main></html>'''
    (ROOT/'research.html').write_text(report,encoding='utf-8')
    print('Wrote research.html with linked reproducible results')

if __name__=='__main__':main()
