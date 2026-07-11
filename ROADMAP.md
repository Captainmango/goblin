# Goblin Roadmap to v1

### Swap to Turso
Using Turso would be good for sync state across everywhere. Should just be a drop in replacement for SQLite3 also.

### AgentFS
Gives filesystem isolation. Should make this opt in of course, but would be a killer feature of an application like this as no ones else is doing it

### Slack/ Telegram tool
Create a tool that allows bidirectional comms between the agent and user using Slack. This is fine for a POC, but probably needs more thought for the next step

### CronScheduling tool
Create a tool that allows an agent to schedule stuff in cron. The idea is that automations need to be push or pull, with push being easier to implement. Cron let's us do this on a schedule really easily so makes sense for V1
