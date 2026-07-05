# Goblin Roadmap to v1

### CronScheduling tool
Create a tool that allows an agent to schedule stuff in cron. The idea is that automations need to be push or pull, with push being easier to implement. Cron let's us do this on a schedule really easily so makes sense for V1

### Slack tool
Create a tool that allows bidirectional comms between the agent and user using Slack. This is fine for a POC, but probably needs more thought for the next step

### Create a proper tool for the swarm db
Currently the swarm db tool is a script emitted in the swarm folder. I'd like this to be a tool so that we can prepare for better toolsets passed to blueprints