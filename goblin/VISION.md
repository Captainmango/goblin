# GOBLIN

## Motivation

Coding agents are run via an attended, chat based flow. This works, but means agents need to be guided directly via a chat session. Maximalist frameworks abstract much of that control away. GOBLIN seeks to be in the middle of these two paradigms.

GOBLIN is an acronym. Generative Orchestration of Basic Logic, Instructions, and Notifications. The intention of the project is to be able to orchestrate prompts sent to a coding agent, with some basic logic overlaying those steps and the ability to notify via a channel.

## Implementation

GOBLIN is a specification directly coupled to the OpenHands SDK. It configures the agent with various settings and supplies a prompt. Each prompt is a step. The spec is designed to be familiar to those that use CI tools like GitHub Actions, or Drone CI. The steps are implemented in a yaml file, called a Blueprint by the system. The system consumes these Blueprints via a CLI interface and runs the sessions with the configurations contained in each step. Each step is isolated from the last, but can make changes to the filesystem in order to preserve state between steps.

## The CLI — Running Blueprints

This repo is the **runtime CLI** that executes blueprints. It is a Python application installed locally via `pipx` — either by running the `install.sh` script directly or through the `/install` skill in OpenHands. Once installed, it is invoked as `goblin run <blueprint> [--context '{}']` (or `goblin validate <blueprint>...` to check a blueprint against the spec without running it). The CLI loads a blueprint, resolves any `imports`, validates it against the spec, and orchestrates step execution through the OpenHands SDK. Each prompt step launches an isolated agent session. Each `load_project` step configures the environment for subsequent prompt steps — loading project settings, CLAUDE.md references, agent definitions, and setting the working directory.

The CLI is where the orchestration logic lives: foreach iteration (integer, Jinja2 expression, or file-backed), conditional execution, failure handling (skip, retry, abort), hook wiring, variable interpolation, structured JSON response parsing with automatic repair, cost/iteration circuit breakers, and checkpointed/resumable runs.

## Authoring Blueprints

A separate project provides an **OpenHands skill** for conversationally authoring blueprints — asking about the target repository, the goal, the steps, variables, hooks, and failure handling, then producing a valid YAML blueprint. It is decoupled from this repo: the only shared contract is the `spec/v1.json` schema. A user can author a blueprint with that skill, then hand the resulting file to this CLI for unattended execution — or write blueprints by hand or with any other tool, since the CLI only depends on the schema being satisfied.

## Design Principles

**Familiar, not novel.** Blueprints look like CI pipeline definitions. Anyone who has written a GitHub Actions workflow or a Drone CI pipeline can read a blueprint without documentation.

**Transparent, not magical.** Each step is a self-contained prompt with explicit tool permissions. There is no hidden state carried between steps — only filesystem changes persist. If a step needs context from a prior step, it reads it from the filesystem or from the structured result via conditions.

**Composable, not monolithic.** `load_project` steps bring in a target project's own OpenHands/ Claude configuration — its settings, its CLAUDE.md, its custom agents. This means Goblin does not replace a project's existing OpenHands setup; it layers orchestration on top of it.

**Fail loudly by default.** The default failure action is `abort`. Steps that can tolerate failure must explicitly opt in to `skip` or `retry`. This prevents silent failures from cascading through a workflow.

