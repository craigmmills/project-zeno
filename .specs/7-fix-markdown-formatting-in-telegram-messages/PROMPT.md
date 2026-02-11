# Feature Spec: Fix markdown formatting in Telegram messages

## Requirements

Telegram messages are showing raw markdown syntax like ** for bold instead of properly formatted text. The Telegram bot should either strip markdown formatting or convert it to Telegram-compatible formatting (MarkdownV2 or HTML) so messages display cleanly without raw markup characters.

## Discussion / Context

### Comment by @craigmmills on 2026-02-11T02:08:52Z

Spec started locally at: /Users/craigmills/Projects/project-zeno/main/.specs/7-fix-markdown-formatting-in-telegram-messages


---

## PLANNING METHODOLOGY — MANDATORY INSTRUCTIONS

> **YOU ARE THE ORCHESTRATOR. YOU MUST FOLLOW THIS METHODOLOGY EXACTLY.**
>
> When the user says "let's draft this" (or any variation like "draft it", "start planning", "go", etc.),
> you MUST execute the 5-stage multi-agent pipeline described below.
>
> **DO NOT write a plan yourself. DO NOT skip stages. DO NOT summarize instead of launching agents.**
> **DO NOT say "I'll draft a plan" — you MUST say "I'll launch 6 parallel sub-agents to draft plans."**
>
> If you write ANY plan content yourself instead of delegating to sub-agents via the Task tool,
> you have FAILED. Your ONLY job is to launch Task agents and wait for them to finish.

> **RULES FOR SUB-AGENTS (include these in every sub-agent prompt):**
>
> - Sub-agents write ALL work to files using Write/Edit tools
> - Sub-agents return ONLY: `Done. Output: [filepath]`
> - Sub-agents MUST write in chunks of ~4000 tokens max (Write tool, then Edit to append)
> - Sub-agents must NEVER return content, summaries, or explanations to the orchestrator
> - Violation of these rules will blow up the orchestrator's context window

---

### Overview

You MUST execute these 5 stages in order. Each stage MUST use the Task tool to launch sub-agents.
You MUST NOT skip any stage. You MUST NOT combine stages. You MUST NOT do the work yourself.

1. **Stage 1**: YOU launch 6 parallel Task agents → each drafts an independent plan
2. **Stage 2**: YOU launch 6 parallel Task agents → each critiques and rewrites a draft
3. **Stage 3**: YOU launch 1 Task agent → synthesizes all drafts + critiques into master plan
4. **Stage 4**: YOU launch 6 parallel Task agents → each simulates implementing the master plan
5. **Stage 5**: YOU launch 1 Task agent → produces final plan from simulation findings

Total: 20 Task agent launches across 5 stages. No shortcuts.

---

### Stage 1: Initial Parallel Drafting

YOU MUST launch 6 Task tool calls in a SINGLE message (parallel execution). Use model "opus".
Each sub-agent gets assigned a number N (1 through 6).

**Prompt for each sub-agent (replace N with 1-6):**

```
CRITICAL SUB-AGENT INSTRUCTIONS:
- You are sub-agent N. You are a SUB-AGENT, not the orchestrator.
- Read PROMPT.md in the current directory for full requirements and context.
- Write a detailed, complete implementation plan to plans/draft_plan_N.md
- Write in chunks of ~4000 tokens maximum. Use Write tool first, then Edit tool to append more content.
- NEVER write more than 4000 tokens in a single tool call — you will hit output limits and lose work.
- Cover: architecture, file changes, data models, API design, error handling, testing strategy, migration plan.
- Be specific — reference actual file paths, function names, and code patterns from the codebase.
- Your final response to the orchestrator must be ONLY these exact words: "Done. Output: plans/draft_plan_N.md"
- Do NOT return any content, summaries, analysis, or explanations. ONLY the done message.
```

**After all 6 complete:** Confirm all 6 files exist, then proceed to Stage 2. Do NOT read the files.

---

### Stage 2: Parallel Critique & Rewrite

YOU MUST launch 6 Task tool calls in a SINGLE message (parallel execution). Use model "opus".
Each sub-agent gets assigned the same number N as their draft.

**Prompt for each sub-agent (replace N with 1-6):**

```
CRITICAL SUB-AGENT INSTRUCTIONS:
- You are sub-agent N. You are a SUB-AGENT, not the orchestrator.
- Read PROMPT.md for the full requirements.
- Read plans/draft_plan_N.md — this is the draft you must critique.
- Write a thorough critique AND an improved version of the plan to plans/critique_N.md
- Critique for: correctness, robustness, missed requirements, poor decisions, missing error handling, gaps in testing, wrong assumptions about the codebase.
- Check completeness against every requirement in PROMPT.md.
- Write in chunks of ~4000 tokens maximum. Use Write tool first, then Edit tool to append.
- Your final response must be ONLY: "Done. Output: plans/critique_N.md"
- Do NOT return any content, summaries, or explanations. ONLY the done message.
```

**After all 6 complete:** Confirm all 6 files exist, then proceed to Stage 3. Do NOT read the files.

---

### Stage 3: Master Plan Synthesis

YOU MUST launch 1 Task tool call. Use model "opus".

**Prompt:**

```
CRITICAL SUB-AGENT INSTRUCTIONS:
- You are a SUB-AGENT, not the orchestrator.
- Read PROMPT.md for the full requirements.
- Read ALL of these files: plans/draft_plan_1.md through plans/draft_plan_6.md AND plans/critique_1.md through plans/critique_6.md (12 files total).
- Synthesize a master implementation plan that:
  - Identifies where drafts AGREE (high confidence decisions)
  - Identifies where drafts DISAGREE (needs resolution — pick the best approach and explain why)
  - Incorporates critique findings and improvements
  - Addresses risks and open questions raised across all drafts
  - Resolves conflicts explicitly with reasoning
- Write the master plan to plans/master_plan_draft.md
- Write in chunks of ~4000 tokens maximum. Use Write tool first, then Edit tool to append.
- Your final response must be ONLY: "Done. Output: plans/master_plan_draft.md"
- Do NOT return any content, summaries, or explanations. ONLY the done message.
```

**After completion:** Confirm the file exists, then proceed to Stage 4. Do NOT read the file.

---

### Stage 4: Parallel Implementation Simulation

YOU MUST launch 6 Task tool calls in a SINGLE message (parallel execution). Use model "opus".

**Prompt for each sub-agent (replace N with 1-6):**

```
CRITICAL SUB-AGENT INSTRUCTIONS:
- You are sub-agent N. You are a SUB-AGENT, not the orchestrator.
- Read PROMPT.md for the full requirements.
- Read plans/master_plan_draft.md — this is the master plan you must simulate implementing.
- Perform a detailed DRY RUN of implementing this entire plan:
  - Walk through each step as if you were actually writing the code
  - Identify what works well and what would break
  - Find missing pieces, unstated assumptions, ordering issues
  - Document gotchas, edge cases, and things the plan doesn't account for
  - Note any steps that are ambiguous or underspecified
- Write your simulation findings to plans/simulation_N.md
- Write in chunks of ~4000 tokens maximum. Use Write tool first, then Edit tool to append.
- Your final response must be ONLY: "Done. Output: plans/simulation_N.md"
- Do NOT return any content, summaries, or explanations. ONLY the done message.
```

**After all 6 complete:** Confirm all 6 files exist, then proceed to Stage 5. Do NOT read the files.

---

### Stage 5: Final Master Plan

YOU MUST launch 1 Task tool call. Use model "opus".

**Prompt:**

```
CRITICAL SUB-AGENT INSTRUCTIONS:
- You are a SUB-AGENT, not the orchestrator.
- Read PROMPT.md for the full requirements.
- Read plans/master_plan_draft.md (the master plan).
- Read ALL of: plans/simulation_1.md through plans/simulation_6.md (6 simulation files).
- Produce the FINAL, COMPLETE implementation plan that:
  - Incorporates all simulation findings
  - Fixes every issue identified during simulation
  - Fills in every gap and ambiguity
  - Is ready to be handed to a developer for implementation with zero questions
  - Includes specific file paths, function signatures, data models, and step-by-step instructions
- Write the final plan to plans/SPEC.md
- Write in chunks of ~4000 tokens maximum. Use Write tool first, then Edit tool to append.
- Your final response must be ONLY: "Done. Output: plans/SPEC.md"
- Do NOT return any content, summaries, or explanations. ONLY the done message.
```

**After completion:** Read plans/SPEC.md and present it to the user. This is the ONLY file you read.

---

### Orchestrator Context Management — CRITICAL

**Your context is precious. Sub-agents have their own 200k token contexts. You do NOT.**

- Your ONLY job is to launch Task agents and confirm they completed. That's it.
- You need ~1k tokens per stage. If your context grows beyond ~20k tokens, you broke a rule.
- NEVER read sub-agent output files yourself (the ONLY exception: plans/SPEC.md at the very end)
- NEVER consume sub-agent return messages beyond confirming the word "Done"
- NEVER write plan content yourself — that's what the 20 sub-agents are for
- If a sub-agent fails, relaunch it. Do NOT do its work yourself as a fallback.


---

## Completion Protocol

When implementation is complete, the implementing agent must:

1. Create or update `DONE.md` in the spec directory.
2. Include the exact test commands run and their outcomes.
3. Summarize file changes and rationale in concise bullet points.
4. List any follow-up risks or deferred work.
