# GOBLIN

## Motivation

Coding agents are run via an attended, chat based flow. This works, but means agents need to be guided directly via a chat session. Maximalist frameworks abstract much of that control away. GOBLIN seeks to be in the middle of these two paradigms.

GOBLIN is an acronym. Generative Orchestration of Basic Logic, Instructions, and Notifications. The intention of the project is to be able to orchestrate prompts sent to a coding agent, with some basic logic overlaying those steps and the ability to notify via a channel.

## Implementation

GOBLIN is a specification directly coupled to Anthropic's Agent SDK (formerly Claude Code SDK). It configures the agent with various settings and supplies a prompt. Each prompt is a step. The spec is designed to be familiar to those that use CI tools like GitHub Actions, or Drone CI. The steps are implemented in a yaml file, called a Blueprint by the system. The system consumes these Blueprints via a CLI interface and runs the sessions with the configurations contained in each step. Each step is isolated from the last, but can make changes to the filesystem in order to preserve state between steps.

## Two Halves of the System

GOBLIN is composed of two distinct, complementary pieces:

### The Skill — Authoring Blueprints

The first half is a **Claude Code skill** that can be forward deployed into any Claude Code installation. This takes the form of a `.claude/` directory containing an agent definition (`agents/workflow-maker.md`) and a reference document (`references/workflow_creation_guidance.md`). When installed into a project, this gives Claude Code the ability to help users design and write blueprints through a conversational flow — asking about the target repository, the goal, the steps, variables, hooks, and failure handling — then producing a valid YAML blueprint.

The skill is portable. It does not require Goblin's runtime to be installed. It only needs to be present in a project's `.claude/` directory for Claude Code to pick it up. This means organisations can distribute the skill to teams who write workflows without requiring those teams to understand Goblin's internals. The skill carries the full schema reference with it, so the authoring agent always produces spec-compliant blueprints.

### The CLI — Running Blueprints

The second half is the **runtime CLI** that actually executes blueprints. It is a Python application invoked via `uv run python main.py <blueprint> [--context '{}']`. The CLI loads a blueprint, validates it against the spec, and orchestrates step execution through the Claude Agent SDK. Each prompt step launches an isolated agent session. Each `load_project` step configures the environment for subsequent prompt steps — loading project settings, CLAUDE.md references, agent definitions, and setting the working directory.

The CLI is where the orchestration logic lives: foreach iteration (integer, Jinja2 expression, or file-backed), conditional execution, failure handling (skip, retry, abort), hook wiring, variable interpolation, and structured JSON response parsing with automatic repair.

### How They Relate

The skill produces the artifact (a blueprint YAML file). The CLI consumes it. A user can author a blueprint with the skill inside Claude Code, then hand that file to the CLI for unattended execution. The two halves share a contract — the `spec/v1.json` schema — but are otherwise decoupled. The skill can be deployed without the CLI, and the CLI can consume blueprints written by hand or by any other tool.

## Design Principles

**Familiar, not novel.** Blueprints look like CI pipeline definitions. Anyone who has written a GitHub Actions workflow or a Drone CI pipeline can read a blueprint without documentation.

**Transparent, not magical.** Each step is a self-contained prompt with explicit tool permissions. There is no hidden state carried between steps — only filesystem changes persist. If a step needs context from a prior step, it reads it from the filesystem or from the structured result via conditions.

**Composable, not monolithic.** `load_project` steps bring in a target project's own Claude Code configuration — its settings, its CLAUDE.md, its custom agents. This means Goblin does not replace a project's existing Claude Code setup; it layers orchestration on top of it.

**Fail loudly by default.** The default failure action is `abort`. Steps that can tolerate failure must explicitly opt in to `skip` or `retry`. This prevents silent failures from cascading through a workflow.

