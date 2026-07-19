# Current Evidence For A Maintainability Ratchet

Literature refresh: 2026-05-23. This memo includes a fresh 2025-2026 pass
over AI-generated code quality, agent maintenance, context retrieval, context
compression, and token-consumption research. Recent arXiv evidence is useful
for direction, but should be treated as provisional until replicated or
peer-reviewed.

## Executive Summary

`riskratchet` is best understood as a regression guardrail, not a proof of
correctness. It gives engineering leads a compact CI and review signal: has
function-level maintainability risk increased beyond the baseline in this
change? The tool computes a per-function score from coverage gaps, branch
coverage gaps, cyclomatic complexity, churn, public surface, and sprawl, then
uses `riskratchet check` to fail when new or existing functions cross configured
regression thresholds [15].

The evidence supports the shape of the product but not a claim that any metric
can certify quality. Cyclomatic complexity is a long-standing path-counting
signal [1], coverage alone has weak empirical relationship to post-release
defects in a large open-source study [2], and defect-prediction research has
long treated process and code metrics as complementary rather than singular
truth [3]. CRAP remains a practical inspiration because it combines complexity
and coverage into an actionable review signal [4], but it is intentionally
narrower than `riskratchet` [15].

The case is stronger in AI-assisted projects. 2026 work reports variable
quality outcomes in AI-generated code [6], measurable AI-introduced technical
debt in real repositories [7], ongoing human maintenance of agent-generated
files [8], and architecture/code-smell risk even when functional correctness is
the primary benchmark [9]. Separately, current agent research shows that
repository context and token budgets are now operational concerns: summarized
and accurately retrieved context can reduce runtime and token cost [11], coding
agent token use can vary sharply across runs [12], context compression can
reduce inference cost [13], and long-context agent benchmarks now evaluate
efficiency as a first-class dimension [14].

Product judgment: for long-running, AI-assisted Python projects, a ratchet is a
practical adoption strategy because most teams cannot stop feature work to make
the entire codebase "good." They can, however, block risk growth, force review
on the riskiest deltas, and require a written reason when the baseline moves up
[15].

## Why Existing Signals Are Not Enough

### Coverage Alone

Evidence-backed claim: coverage is useful for knowing what test execution
touched, but it is not a quality certificate. Kochhar, Lo, Lawall, and Nagappan
studied 100 large open-source Java projects and found insignificant correlation
between coverage and post-release bugs at the project level, and no such
correlation at file level [2]. Savoia's CRAP writeup also warns that high
coverage can coexist with poor tests [4].

Product judgment: `riskratchet` should treat coverage gaps as one component,
not the final answer. That matches the implementation: `coverage_gap` is 30% of
the default score, `branch_gap` is 15%, and both are blended with structure,
churn, public surface, and sprawl [15].

### Complexity Alone

Evidence-backed claim: cyclomatic complexity is a useful measure of independent
paths through a program [1]. It is not the same as defect probability or
business risk. Some domains genuinely need branching, and complexity becomes
more concerning when combined with weak tests, high churn, public exposure, or
large surrounding files [3][4][5].

Product judgment: `riskratchet` should not fail simply because code is complex.
The useful question is whether the function became riskier relative to the
team's accepted baseline, or whether a new function enters above the configured
threshold [15].

### CRAP Is Valuable But Narrow

Evidence-backed claim: CRAP combines cyclomatic complexity and coverage with
the formula `comp(m)^2 * (1 - cov(m)/100)^3 + comp(m)` and was designed as a
practical way to flag complex, poorly tested methods [4]. The modern
`cargo-crap` article makes the same operational point for Rust and AI-assisted
workflows: the signal is strongest when it turns broad coverage reports into a
ranked list of functions needing attention [5].

Product judgment: CRAP should remain in `riskratchet` output because it is
familiar and useful, but it should not define the whole score. The local product
already reports CRAP while computing a broader six-component risk score [15].
That broader score captures several dimensions CRAP omits: branch gaps, churn,
public/private exposure, and sprawl [15].

## Why A Baseline Ratchet

Evidence-backed claim: defect prediction research has repeatedly compared
multiple families of code and process signals rather than relying on one
universal score [3]. Code-smell research also suggests that structural design
and implementation problems affect change- and fault-proneness, but these
signals are probabilistic and context-sensitive rather than absolute judgments
[16].

Product judgment: a baseline ratchet is the right adoption mechanism for a
mature codebase. It starts from "do not make it worse" instead of forcing a team
to agree on one global quality threshold before getting value. In
`riskratchet`, the baseline stores the current per-function score and component
values; `compare` flags new high-risk functions, score regressions, and
component regressions beyond configured tolerances [15].

This framing matters. `riskratchet check` should be sold and used as a
regression detector, not a global quality grade. A high score should prompt
review, simplification, or test work. It should not be treated as proof that the
function is broken. Conversely, a low score does not prove correctness; it only
means the measured maintainability signals are currently low [1][2][4].

## Why This Matters More With AI Agents

Evidence-backed claim: current AI code-quality research is mixed rather than
unconditionally positive. A 2026 synthesis found that AI-generated code quality
varies across correctness, security, maintainability, and complexity outcomes,
and depends on task specification, prompting, developer expertise, and workflow
validation [6]. A large-scale 2026 study of verified AI-authored commits found
AI-introduced issues in production repositories, with code smells dominating
the issue set and a meaningful share persisting to the latest revision [7].

Evidence-backed claim: autonomous agent output is not maintenance-free. An
EASE 2026 accepted empirical study of agent-generated code analyzed more than
1,000 files and about 3,200 changes across 100 popular repositories, finding
that human developers performed the large majority of subsequent maintenance
[8]. A 2026 study of failed agent-authored pull requests found that not-merged
agent PRs often involved larger code changes, touched more files, and failed
CI/CD validation more often than successful ones [17].

Evidence-backed claim: functional correctness benchmarks can miss long-term
maintainability. Recent surveys and preprints argue that repository-level
context, long-horizon consistency, verification, security, and maintainability
remain unresolved problems for AI coding systems [9][10].

Product judgment: these findings make a ratchet more valuable, not because AI
agents are uniquely incapable, but because they can change code quickly and can
leave behind code that passes narrow tests while growing harder to inspect,
summarize, and repair. `riskratchet` gives reviewers and agents a small target
list: functions whose measured risk increased and the components responsible
for the increase [15].

## Token Efficiency And Repository Context

Evidence-backed claim: token efficiency is now a software-engineering
management issue, not just an LLM platform detail. A 2026 token-consumption
study on agentic coding tasks reports that agent runs consume far more tokens
than code chat/reasoning tasks, that input tokens drive much of the cost, that
repeat runs can vary sharply, and that higher token use does not reliably mean
higher accuracy [12].

Evidence-backed claim: context selection quality matters. SWE-ContextBench
evaluates whether coding agents can reuse related prior context and reports
that accurately summarized and retrieved experience can improve resolution
accuracy while reducing runtime and token cost, especially on harder tasks;
unfiltered or incorrectly selected context can be neutral or harmful [11].
Context-compression work similarly reports that repository-level code tasks
need long multi-file context and that compression can reduce inference cost and
latency [13]. LoCoBench-Agent evaluates long-context software-engineering
agents across contexts from 10K to 1M tokens and explicitly measures tool-use
and conversation efficiency [14].

Product judgment: a per-function ratchet is a small context artifact that can
make agents cheaper to steer. Instead of repeatedly asking an agent to reread a
whole repository and infer where risk grew, CI can hand it a structured list:
path, qualified function name, score delta, and component deltas. That does not
replace architecture understanding, but it reduces the first pass from broad
repo discovery to focused repair [11][12][13][15].

## What Might Be Wrong

Metrics can create false precision. A score like `63.4` looks exact, but the
underlying risk model is heuristic. McCabe complexity, coverage, churn, and
smell metrics are useful correlates, not proofs [1][2][3][16].

Coverage can be shallow. A line can execute without an assertion that catches
the behavior that matters, so both CRAP and `riskratchet` can understate risk
when tests are broad but weak [2][4].

Churn can reflect active ownership rather than danger. Defect-prediction work
uses process history because it can help explain risk, but process signals are
context-sensitive and should not be read as blame [3].

Public/private heuristics can misclassify API boundaries. Python visibility is
conventional, framework callbacks blur API surfaces, and internal functions can
be mission critical. That makes `public_surface` a review prompt, not a
semantic guarantee [15].

Sprawl can over-penalize generated, vendor, migration, or framework-shaped
code. The default config excludes common generated and migration paths, and
teams should extend those exclusions when the measured code is not an
appropriate maintainability target [15]. The empirical calibration thread (P21)
further flags the *file-line* half of sprawl as a likely size confound; dropping
or shrinking it is the model-supported candidate for the 0.3.0 weight review (see
`docs/sprawl-component-finding.md`).

Token-efficient summaries can hide architecture. Context research supports
summarization and compression when the right context is selected, but it also
warns that irrelevant or incorrectly selected context can hurt [11][13]. A
short risk list is a starting point, not a substitute for reading the relevant
design and call graph.

Teams can misuse baselines by bumping them to silence regressions. The README
already calls this out as a common mistake and recommends a dedicated PR with
written justification when the baseline must move up [15].

## Recommendations

Start with baseline regression gating. Create the baseline on `main`, then
fail CI only when measured risk grows beyond tolerance or a new function enters
above the new-function threshold [15].

Treat high scores as review prompts. Ask whether the function needs simpler
control flow, better branch coverage, narrower public exposure, or a follow-up
refactor. Do not treat the score as a defect by itself [1][2][4].

Exclude generated, vendor, migration, and framework boilerplate. This keeps the
signal on code the team can reasonably improve [15].

Require justification for baseline bumps. A baseline increase should explain
why the risk is intentional, temporary, or cheaper than the alternatives [15].

Keep weights configurable but conservative by default. The current defaults
weight coverage and structure most heavily while preserving branch, churn,
public-surface, and sprawl signals; overrides are validated and renormalized so
typos or negative weights cannot silently weaken the model [15]. (The file-line
component of sprawl is under empirical review for the 0.3.0 weight recalibration; see P21.)

Re-run the literature check before publishing this externally. The 2026 AI-code
and agent-efficiency literature is moving quickly; preprints should be labeled
as provisional, and peer-reviewed updates should replace them when available
[6][7][8][9][11][12][13][14][17].

## Bibliography

[1] Thomas J. McCabe, "A Complexity Measure," 1976, peer-reviewed, IEEE
Transactions on Software Engineering. DOI:
https://doi.org/10.1109/TSE.1976.233837. Relevance: introduces cyclomatic
complexity as a graph-theoretic measure of program control-flow paths.

[2] Pavneet Singh Kochhar, David Lo, Julia Lawall, and Nachiappan Nagappan,
"Code Coverage and Postrelease Defects: A Large-Scale Study on Open Source
Projects," 2017, peer-reviewed, IEEE Transactions on Reliability. DOI:
https://doi.org/10.1109/TR.2017.2727062; repository page:
https://ink.library.smu.edu.sg/sis_research/3838/. Relevance: empirical
evidence that coverage alone is not a reliable defect proxy.

[3] Marco D'Ambros, Michele Lanza, and Romain Robbes, "An Extensive Comparison
of Bug Prediction Approaches," 2010, peer-reviewed, 7th IEEE Working
Conference on Mining Software Repositories. DOI:
https://doi.org/10.1109/MSR.2010.5463279. Relevance: establishes that defect
prediction uses multiple code/process metric families and benchmarks rather
than one universal signal.

[4] Alberto Savoia, "This Code is CRAP," 2011, practitioner article, Google
Testing Blog. URL:
https://testing.googleblog.com/2011/02/this-code-is-crap.html. Relevance:
practical origin and limitations of the CRAP metric.

[5] Oleksandr Prokhorenko, "cargo-crap: Finding Untested Complexity in
AI-Generated Rust Code," 2026, practitioner article. URL:
https://minikin.me/blog/cargo-crap. Relevance: modern implementation and
workflow framing for CRAP as an AI-assisted review guardrail.

[6] Vehid Geruslu, Zulfiyya Aliyeva, and Eray Tuzun, "Factors Influencing the
Quality of AI-Generated Code: A Synthesis of Empirical Evidence," 2026,
preprint. DOI: https://doi.org/10.48550/arXiv.2603.25146. Relevance:
synthesizes empirical findings about correctness, security, maintainability,
complexity, and human factors in AI-generated code.

[7] Yue Liu, Ratnadira Widyasari, Yanjie Zhao, Ivana Clairine Irsan, Junkai
Chen, and David Lo, "Debt Behind the AI Boom: A Large-Scale Empirical Study of
AI-Generated Code in the Wild," 2026, preprint. DOI:
https://doi.org/10.48550/arXiv.2603.28592. Relevance: studies AI-authored
commits and persistence of AI-introduced code-quality issues.

[8] Shota Sawada, Tatsuya Shirai, Yutaro Kashiwa, Ken'ichi Yamaguchi, Hiroshi
Iwata, and Hajimu Iida, "To What Extent Does Agent-generated Code Require
Maintenance? An Empirical Study," 2026, peer-reviewed accepted paper with
preprint copy, EASE 2026. DOI: https://doi.org/10.48550/arXiv.2605.06464.
Relevance: measures maintenance of agent-generated files versus human-authored
code.

[9] Yuecai Zhu, Nikolaos Tsantalis, and Peter C. Rigby, "AI-Generated Smells:
An Analysis of Code and Architecture in LLM and Agent-Driven Development,"
2026, preprint. DOI: https://doi.org/10.48550/arXiv.2605.02741. Relevance:
argues that functional correctness can miss structural and architectural
maintainability issues in generated code.

[10] Burak Gulmez, "Code Generation with Large Language Models: A Survey from
Neural Program Synthesis to Autonomous Software Development," 2026,
peer-reviewed, Applied Intelligence. DOI:
https://doi.org/10.1007/s10489-026-07230-0. Relevance: surveys LLM code
generation and identifies repository-level context, consistency, verification,
security, and long-term quality as open problems.

[11] Jiayuan Zhu, Junde Wu, Minhao Hu, Shengda Zhu, Jiazhen Pan, Weixiang Shen,
Yijun Yang, Fenglin Liu, Jianye Hao, Yueming Jin, Qirong Ho, and Min Xu,
"SWE Context Bench: A Benchmark for Context Learning in Coding," 2026,
benchmark/preprint. DOI: https://doi.org/10.48550/arXiv.2602.08316.
Relevance: evaluates context retrieval and reuse for coding agents, including
runtime and token-cost effects.

[12] Longju Bai, Zhemin Huang, Xingyao Wang, Jiao Sun, Rada Mihalcea, Erik
Brynjolfsson, Alex Pentland, and Jiaxin Pei, "How Do AI Agents Spend Your
Money? Analyzing and Predicting Token Consumption in Agentic Coding Tasks,"
2026, preprint. DOI: https://doi.org/10.48550/arXiv.2604.22750. Relevance:
quantifies token-consumption variability and economics in coding-agent tasks.

[13] Jia Feng, Zhanyue Qin, Cuiyun Gao, Ruiqi Wang, Chaozheng Wang, Yingwei Ma,
and Xiaoyuan Xie, "On the Effectiveness of Context Compression for
Repository-Level Tasks: An Empirical Investigation," 2026, preprint. DOI:
https://doi.org/10.48550/arXiv.2604.13725. Relevance: evaluates context
compression approaches for repository-level code tasks and efficiency.

[14] Jielin Qiu, Zuxin Liu, Zhiwei Liu, Rithesh Murthy, Jianguo Zhang, Haolin
Chen, Shiyu Wang, Ming Zhu, Liangwei Yang, Juntao Tan, Roshan Ram, Akshara
Prabhakar, Tulika Awalgaonkar, Zixiang Chen, Zhepeng Cen, Cheng Qian, Shelby
Heinecke, Weiran Yao, Silvio Savarese, Caiming Xiong, and Huan Wang,
"LoCoBench-Agent: An Interactive Benchmark for LLM Agents in Long-Context
Software Engineering," 2025, benchmark/preprint. DOI:
https://doi.org/10.48550/arXiv.2511.13998. Relevance: evaluates multi-turn
long-context coding agents, including tool-use and conversation efficiency.

[15] `riskratchet` repository behavior, 2026, product/repo evidence. Local
paths: `README.md`, `AGENTS.md`, `src/riskratchet/scoring.py`,
`src/riskratchet/baseline.py`, `pyproject.toml`, and `schemas/`. Relevance:
documents and implements the CLI, baseline ratchet, scoring components, output
contracts, weights, exclusions, and agent-facing workflow.

[16] Fabio Palomba, Gabriele Bavota, Massimiliano Di Penta, Fausto Fasano,
Rocco Oliveto, and Andrea De Lucia, "On the Diffuseness and the Impact on
Maintainability of Code Smells: A Large Scale Empirical Investigation," 2018,
peer-reviewed, Empirical Software Engineering. DOI:
https://doi.org/10.1007/s10664-017-9535-z. Relevance: empirical support that
design and implementation smells, especially long/complex code, relate to
change- and fault-proneness.

[17] Ramtin Ehsani, Sakshi Pathak, Shriya Rawal, Abdullah Al Mujahid, Mia
Mohammad Imran, and Preetha Chatterjee, "Where Do AI Coding Agents Fail? An
Empirical Study of Failed Agentic Pull Requests in GitHub," 2026, preprint.
DOI: https://doi.org/10.48550/arXiv.2601.15195. Relevance: studies 33k
agent-authored pull requests and rejection patterns in real repositories.
