# ShadowFrog

## Overview

ShadowFrog is a suite of AI coding agent skills that builds and maintains a shadow knowledge base for any software codebase. It turns idle coding-agent time into autonomous discovery loops: the agent explores source code, runs experiments in isolated branches, and records behavioral insights (edge cases, implicit contracts, cross-file interactions) in a structured .shadow/ directory that mirrors the repository. Knowledge compounds across sessions, so an agent returning to the same codebase can recall what it previously learned rather than rediscovering it from scratch.

ShadowFrog is implemented entirely as prompt instructions and lightweight helper scripts (Python, Bash) that plug into existing AI agent harnesses such as GitHub Copilot CLI and Claude Code. It does not bundle or fine-tune any machine learning models; it relies on the host agent's LLM for reasoning. The system also captures knowledge shared by human developers during conversations, treating user-provided insights as the highest-trust source. All data is stored locally in plain-text Markdown and JSON files within the repository, with no external service dependencies beyond the configured git remote.

### What Can ShadowFrog Do

ShadowFrog was developed to give AI coding agents a persistent, compounding memory of the codebases they work in. Without it, every agent session starts from zero; with it, prior discoveries about how the code actually behaves carry forward. Specifically, ShadowFrog enables an agent to: (1) initialize a shadow knowledge base by scanning a repository's source files and extracting its symbol structure; (2) autonomously explore and experiment with the codebase during idle time ("dreaming"), recording behavioral findings such as hidden edge cases, implicit contracts between modules, and latent bugs; (3) capture knowledge shared by human developers during normal coding conversations; (4) navigate and query the accumulated knowledge base at the symbol level when working on future tasks; and (5) maintain the shadow over time through incremental updates, deduplication, and conflict resolution.

The system is designed for a research audience studying how AI agents can build and leverage long-term understanding of software. It operates entirely within the user's local repository and git workflow, producing human-readable Markdown artifacts. During autonomous exploration ShadowFrog may write and run throwaway experiments in isolated, disposable git branches, but it does not merge or ship that experimental code into your production branches on its own — only the resulting behavioral discoveries (not the experiment code) are integrated into the knowledge base; it builds a knowledge layer that the host agent can consult when performing downstream tasks such as bug fixing, code review, or feature planning.

A detailed discussion of ShadowFrog, including how it was developed and tested, can be found in our [blog post](https://microsoft.github.io/debug-gym/blog/2026/06/shadow-frog/).

### Intended Uses

ShadowFrog is best suited for software developers and researchers who want to give their AI coding agents persistent, compounding knowledge about a codebase. Typical use cases include running autonomous exploration to surface latent bugs or architectural patterns, equipping an agent with pre-built context before bug-fixing or feature-planning tasks, and capturing institutional knowledge from developer conversations so it survives beyond a single session.

ShadowFrog is being shared with the research community to facilitate reproduction of our results and foster further research in this area.

ShadowFrog is intended to be used by domain experts who are independently capable of evaluating the quality of outputs before acting on them. Shadow discoveries are agent-generated behavioral claims that may be incorrect or stale; developers should treat them as hypotheses to verify, not ground truth.

### Out-of-Scope Uses

ShadowFrog is not well suited for fully autonomous code deployment without human review, safety-critical systems where unverified behavioral claims could mask defects, or as a substitute for formal verification, static analysis, or security auditing tools.

We do not recommend using ShadowFrog in commercial or real-world applications without further testing and development. It is being released for research purposes.

ShadowFrog was not designed or evaluated for all possible downstream purposes. Developers should consider its inherent limitations as they select use cases, and evaluate and mitigate for accuracy, safety, and fairness concerns specific to each intended downstream use.

Without further testing and development, ShadowFrog should not be used in sensitive domains where inaccurate outputs could suggest actions that lead to injury or negatively impact an individual's legal, financial, or life opportunities.

We do not recommend using ShadowFrog in the context of high-risk decision making (e.g. in law enforcement, legal, finance, or healthcare).

## How to Get Started

To begin using ShadowFrog, follow the installation and usage instructions in the repository [README.md](https://github.com/microsoft/ShadowFrog/blob/main/README.md).

## Evaluation

ShadowFrog was evaluated on its ability to: (1) navigate and retrieve relevant shadow knowledge given a file path (read-path recall); (2) independently discover known real-world bugs through autonomous exploration without being given a problem statement (blind bug hunting on SWE-Bench Verified and at scale on SWE-Smith); (3) improve bug-fix success rates by providing pre-built shadow context to a coding agent (bug fixing on SWE-Bench Verified); and (4) generate higher-quality, more architecturally grounded feature ideas compared to a no-shadow baseline (feature ideation across 8 open-source repositories, blind-judged by an ensemble of three LLMs).

A detailed discussion of our evaluation methods and results can be found in our [blog post](https://microsoft.github.io/debug-gym/blog/2026/06/shadow-frog/).

### Evaluation Methods

We used recall (file/function level), LLM-judge verdict rates, alignment to shipped features, and blind-judged quality scores (Groundedness, Insight, User Impact, Spec Clarity) to measure ShadowFrog's performance.

We compared the performance of ShadowFrog against a matched no-shadow baseline using SWE-Bench Verified, SWE-Smith, and a feature ideation benchmark across 8 open-source repositories.

The model used for evaluation was Claude Opus 4.6, running inside the GitHub Copilot CLI agent harness. Cross-LLM robustness was verified with Claude Opus 4.7 and GPT-5.5 as independent judges.

Results may vary if ShadowFrog is used with a different model based on its unique design, configuration, and training.

### Evaluation Results

At a high level, we found that ShadowFrog performed strongly on knowledge retrieval and blind bug discovery, modestly on bug fixing, and with a distinctive quality profile on feature ideation. Specifically: the read path achieves ~98% recall at realistic tool-call budgets. On blind bug hunting (no problem statement given), the agent independently locates 88% of real-world bugs to the correct subsystem and 22% exactly, purely from idle-time exploration; at scale (20 repos × 100 stacked bugs), it leads the no-shadow baseline by +25.4 percentage points at peak. On bug fixing (50 SWE-Bench Verified tasks), ShadowFrog resolves 82.0% vs the baseline's 77.3% (+4.7 pp), though most of the lift traces to the structured workflow rather than shadow content itself, highlighting a consumption bottleneck we plan to address. On feature ideation (3,310 ideas blind-judged), ShadowFrog generates ideas rated higher on insight (+0.40) and user impact (+0.24), while trading off slightly on specification clarity, a gap that largely dissolves when controlling for problem size. We refer readers to our [blog post](https://microsoft.github.io/debug-gym/blog/2026/06/shadow-frog/) for detailed evaluation results.

## Limitations

ShadowFrog was developed for research and experimental purposes. Further testing and validation are needed before considering its application in commercial or real-world scenarios.

ShadowFrog was designed and tested using the English language. Performance in other languages may vary and should be assessed by someone who is both an expert in the expected outputs and a native speaker of that language.

Outputs generated by AI may include factual errors, fabrication, or speculation. Users are responsible for assessing the accuracy of generated content. All decisions leveraging outputs of the system should be made with human oversight and not be based solely on system outputs.

ShadowFrog inherits any biases, errors, or omissions produced by its base model. Developers are advised to choose an appropriate base LLM/MLLM carefully, depending on the intended use case.

There has not been a systematic effort to ensure that systems using ShadowFrog are protected from security vulnerabilities such as indirect prompt injection attacks. Any systems using it should take proactive measures to harden their systems as appropriate.

Shadow staleness. Discoveries are anchored to specific code symbols and file paths. As the codebase evolves, shadows can become stale or reference code that no longer exists. The system includes staleness detection (comparing the last-update commit to HEAD), but users should not assume older discoveries remain accurate without re-verification.

Plain-text persistence. All shadow content (including user-shared knowledge) is stored as unencrypted Markdown files within the repository. If the .shadow/ directory is committed and pushed, its contents become visible to anyone with repository access. Users should avoid sharing sensitive information (credentials, PII, proprietary business logic) through the shadow capture mechanism.

## Best Practices

Better performance can be achieved by running multiple compounding dream sessions rather than a single long one, keeping the shadow up to date after significant code changes, and capturing domain knowledge from developers during conversations. Directing exploration toward under-explored, high-fan-in areas of the codebase (via the coverage map) also improves discovery yield.

We strongly encourage users to use LLMs/MLLMs that support robust Responsible AI mitigations, such as Azure Open AI (AOAI) services. Such services continually update their safety and RAI mitigations with the latest industry standards for responsible use. For more on AOAI's best practices when employing foundations models for scripts and applications:

- [What is Azure AI Content Safety?](https://learn.microsoft.com/en-us/azure/ai-services/content-safety/overview)
- [Overview of Responsible AI practices for Azure OpenAI models](https://learn.microsoft.com/en-us/legal/cognitive-services/openai/overview)
- [Azure OpenAI Transparency Note](https://learn.microsoft.com/en-us/legal/cognitive-services/openai/transparency-note)
- [OpenAI's Usage policies](https://openai.com/policies/usage-policies)
- [Azure OpenAI's Code of Conduct](https://learn.microsoft.com/en-us/legal/cognitive-services/openai/code-of-conduct)

## License

MIT License

Nothing disclosed here, including the Out of Scope Uses section, should be interpreted as or deemed a restriction or modification to the license the code is released under.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft trademarks or logos is subject to and must follow Microsoft's Trademark & Brand Guidelines. Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship. Any use of third-party trademarks or logos are subject to those third-party's policies.

## Contact

This research was conducted by members of [Microsoft Research](https://www.microsoft.com/en-us/research/). We welcome feedback and collaboration from our audience. If you have suggestions, questions, or observe unexpected/offensive behavior in our technology, please contact us at [debug-gym@microsoft.com](mailto:debug-gym@microsoft.com)

If the team receives reports of undesired behavior or identifies issues independently, we will update this repository with appropriate mitigations.
