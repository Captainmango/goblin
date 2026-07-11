# Goblin Roadmap to v1

### CronScheduling tool
Create a tool that allows an agent to schedule stuff in cron. The idea is that automations need to be push or pull, with push being easier to implement. Cron let's us do this on a schedule really easily so makes sense for V1

### Slack/ Telegram tool
Create a tool that allows bidirectional comms between the agent and user using Slack. This is fine for a POC, but probably needs more thought for the next step

### Create a proper tool for the swarm db
Currently the swarm db tool is a script emitted in the swarm folder. I'd like this to be a tool so that we can prepare for better toolsets passed to blueprints

### Swap to Turso
Using Turso would be good for sync state across everywhere. Should just be a drop in replacement for SQLite3 also.

### AgentFS
Gives filesystem isolation. Should make this opt in of course, but would be a killer feature of an application like this as no ones else is doing it

### Worktree support
We want agents to work on a worktree basis. The best case here is to have a base install we have set up then shard it to different AgentFS systems rather than symlinks. This means that cleanup is super simple and means we can have durable local with state persisted to remote git server over maintaining branches locally. There is a caveat here in that if agents try to push the same branch, the Git server might not be atomic, but that shouldn't come up often enough to matter.