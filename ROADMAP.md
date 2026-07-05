# Goblin Roadmap

## Enhancements

### Structured Step Output Schema
Allow steps to declare an `output_schema` (JSON Schema fragment) that the runner validates the agent's response against. This would make data flow between steps explicit and catch mis-shaped outputs early rather than letting a bad value silently propagate.

### Swarm Agent Roles
Allow swarm agents to declare a list of `roles` (e.g. `["backend", "database"]`) and allow tasks to specify a required `role`. The coordinator and agents would use this to assign and filter tasks by specialisation rather than relying entirely on agent names.

### Generic Notifications system
Currently relying on OpenHands' agent canvas for this. For POC that makes sense. If we want to make this useful for other people, we need a simple way to send messages for human-in-the-loop workflows and decision gates.
