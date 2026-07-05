# Goblin Roadmap

## Enhancements

### Swarm Agent Roles
Allow swarm agents to declare a list of `roles` (e.g. `["backend", "database"]`) and allow tasks to specify a required `role`. The coordinator and agents would use this to assign and filter tasks by specialisation rather than relying entirely on agent names.

### Generic Notifications system
Currently relying on OpenHands' agent canvas for this. For POC that makes sense. If we want to make this useful for other people, we need a simple way to send messages for human-in-the-loop workflows and decision gates.
