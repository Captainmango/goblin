# Make GOBLIN amazing

MAKE THE PAIRING STRING SHORTER (LIKE 8 CHAR LONG)

- [ ] Kick the tires on features added
    - [ ] Worktrees
    - [ ] Notifications 
        - [ ] Asking and answering questions
        - [ ] Alerting user to things like circuitbreaker firing
    - [ ] JSON schema output
    - [ ] Circuit breaker
    - [ ] Blueprint imports
    - [ ] Browser use???

- [ ] Documentation site
    - [x] Full docs site using Zensical
    - [x] All docs in markdown and a links map for AI
    - [ ] Proof read and improve it for human readers
    - [ ] Can Getting started be always expanded?
    - [ ] Map for AI Agents?
    - [ ] Host on Github pages (GHA to build it)

- [x] Skill that knows about all the documentation and is an expert on building blueprints
    - [x] Docs map and docs follow the skill
    - [x] Should be installable via skills.sh or some other repo

- [ ] Agent Server (Remote runtime with all the GOBLIN stuff)
    - [ ] Should be an ASGI app (Probs just fork the existing server)
    - [ ] Needs all the GOBLIN stuff in it
    - [ ] Test with Compose set up

- [ ] Set up remote box on K8s (Need to do the other k8s stuff first)
    - [ ] Add AgentSandbox CRDs
    - [ ] Workout if things like AgentFS even work inside it
        - [ ] Do we want to treat the box like cattle or a pet?

- [ ] Do we need an eventbus? Webhooks for timing stuff?
    - [ ] How do we coordinate remote runtimes to start jobs
    - [ ] Probs need a way to state that a Blueprint is to run on a schedule/ event trigger
    - [ ] How do we consume from an issue tracker?

- [ ] Look at running GOBLIN on the BEAM and how we get there
    - [ ] Hornbeam looks like a solid bet
    - [ ] Work out if we Gleam/ Elixir or have to use Erlang
    - [ ] How do we package the runtime and hook local to remote cluster? Do we even need this?
    - [ ] Do we need to rebuild the agent? Current Impl is soft coupled to OpenHands, but isn't too crazy to remove
    - [ ] Tool streaming using hot reload feature of the beam