# Architecture

This page describes the high-level component diagram of Goblin.

## Component Diagram

```
┌─────────────────┐
│   CLI (click)   │
│  goblin/cli.py  │
└────────┬────────┘
         │
         ▼
┌──────────────────────┐
│  BlueprintRunner     │
│  goblin/runner.py    │
└────────┬─────────────┘
         │
         ▼
┌──────────────────────┐
│  V1Blueprint         │
│  goblin/blueprint.py │
└────────┬─────────────┘
         │
         ▼
┌──────────────────────────┐
│  PhaseOrchestrator       │
│  goblin/orchestrator.py  │
└────────┬─────────────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌────────┐ ┌─────────┐
│ Prompt │ │  Swarm  │
│ Phase  │ │  Phase  │
└───┬────┘ └────┬────┘
    │           │
    ▼           ▼
┌──────────────────────┐
│  OpenHands SDK       │
│  Agent / Conversation│
└──────────────────────┘
```

## Key Components

### CLI (`goblin/cli.py`)

The Click-based command-line interface. Handles argument parsing, logging setup, and dispatches to `BlueprintRunner`.

### BlueprintRunner (`goblin/runner.py`)

Loads a blueprint YAML file, resolves imports, validates against the JSON Schema, and constructs the appropriate blueprint class (`V1Blueprint`).

### V1Blueprint (`goblin/blueprint.py`)

Parses the blueprint spec into phase objects, resolves LLM/workspace/hook configuration, and delegates execution to `PhaseOrchestrator`.

### PhaseOrchestrator (`goblin/orchestrator.py`)

Runs the phase graph: handles dependency waves, condition evaluation, foreach iteration, checkpointing, and failure recovery.

### PromptPhase (`goblin/phase.py`)

Executes a single prompt phase by constructing an OpenHands `Agent`, running a conversation, and parsing the response.

### SwarmPhase (`goblin/swarm.py`)

Orchestrates multi-agent swarms with a coordinator-worker pattern backed by `SwarmDB`.

### OpenHands SDK

Goblin builds on the OpenHands SDK for LLM conversations, tool execution, and workspace management.
