# GOBLIN

## Motivation

GOBLIN is an acronym. Generative Orchestration of Basic Logic, Instructions, and Notifications. The intention of the project is to be able to orchestrate prompts to a coding agent, with some basic logic overlaying those steps and the ability to notify via a channel.

Coding agents are run via an attended, chat based flow. This works, but means agents need to be guided directly via a chat session. Maximalist frameworks abstract much of that control away. GOBLIN seeks to be in the middle of these two paradigms.

## Implementation

GOBLIN is a specification directly coupled to Anthropic's Agent SDK (formerly Claude Code SDK). It configures the agent with various settings and supplies a prompt. Each prompt is a step. The spec is designed to be familiar to those that use CI tools like GitHub Actions, or Drone CI. The steps are implemented in a yaml file, called a Blueprint by the system. The system consumes these blueprints via a CLI interface and runs the sessions with the configurations contained in each step. Each step is isolated from the last, but can make changes to the filesystem in order to preserve state between steps.

The logic is basic boolean logic based on JINJA2 syntax, allowing for basic checks on parsed JSON responses from steps.

There is also step retry logic. Should a step fail, it will retry.

