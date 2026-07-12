from __future__ import annotations

from goblin_cli.phase import LoadProjectPhase, PhaseProtocol, PromptPhase


class DependencyGraph:
    """Validates and exposes the dependency structure of a blueprint's phases.

    Operates purely on phase.id / .depends_on / .history / .foreach — no Jinja,
    hooks, or I/O. Constructed once at blueprint parse time.
    """

    def __init__(self, phases: list[PhaseProtocol]):
        self.phases = phases
        self._compute_default_dependencies()
        self._validate_dependencies()
        self._validate_history()

    def _compute_default_dependencies(self):
        """Set default depends_on for phases that don't explicitly declare it."""
        for i, phase in enumerate(self.phases):
            if phase.depends_on is None:
                if i == 0:
                    phase.depends_on = []
                else:
                    phase.depends_on = [self.phases[i - 1].id]

    def _validate_dependencies(self):
        """Validate phase dependencies: existence, cycles, and load_project ordering."""
        phase_ids = {p.id for p in self.phases}

        # Existence check
        for phase in self.phases:
            assert phase.depends_on is not None
            for dep_id in phase.depends_on:
                if dep_id not in phase_ids:
                    raise ValueError(f"Phase '{phase.id}': dependency '{dep_id}' does not exist")

        # Cycle detection
        self._detect_cycles()

        # LoadProject total ordering
        load_project_phases = [p for p in self.phases if isinstance(p, LoadProjectPhase)]
        for i, lp1 in enumerate(load_project_phases):
            for lp2 in load_project_phases[i + 1 :]:
                deps1 = self.transitive_deps(lp1.id)
                deps2 = self.transitive_deps(lp2.id)
                if lp2.id not in deps1 and lp1.id not in deps2:
                    raise ValueError(
                        f"LoadProject phases '{lp1.id}' and '{lp2.id}' are independent. "
                        "All load_project phases must be totally ordered by dependencies."
                    )

    def _detect_cycles(self):
        """Detect cycles in the dependency graph using DFS."""
        id_to_phase = {p.id: p for p in self.phases}
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {p.id: WHITE for p in self.phases}

        def dfs(node_id: str):
            color[node_id] = GRAY
            deps = id_to_phase[node_id].depends_on
            assert deps is not None
            for dep_id in deps:
                if color[dep_id] == GRAY:
                    raise ValueError(f"Cycle detected in phase dependencies involving '{node_id}' and '{dep_id}'")
                if color[dep_id] == WHITE:
                    dfs(dep_id)
            color[node_id] = BLACK

        for phase in self.phases:
            if color[phase.id] == WHITE:
                dfs(phase.id)

    def transitive_deps(self, phase_id: str) -> set[str]:
        """Compute transitive dependency closure for a phase."""
        id_to_phase = {p.id: p for p in self.phases}
        visited: set[str] = set()

        def dfs(node_id: str):
            deps = id_to_phase[node_id].depends_on
            assert deps is not None
            for dep_id in deps:
                if dep_id not in visited:
                    visited.add(dep_id)
                    dfs(dep_id)

        dfs(phase_id)
        return visited

    def _validate_history_reference(
        self, phase: PhaseProtocol, ref_id: str, phase_ids: set[str], foreach_phase_ids: set[str]
    ) -> None:
        """Validate a single `history` reference on *phase*. Raises ValueError on any violation."""
        if ref_id not in phase_ids:
            raise ValueError(
                f"Phase '{phase.id}': history references '{ref_id}' which is not defined before this phase"
            )

        if ref_id in foreach_phase_ids:
            raise ValueError(f"Phase '{phase.id}': history cannot reference foreach phase '{ref_id}'")

        referenced = next(s for s in self.phases if s.id == ref_id)
        if not isinstance(referenced, PromptPhase):
            raise ValueError(
                f"Phase '{phase.id}': history can only reference prompt "
                f"phases, but '{ref_id}' is a {type(referenced).__name__}"
            )

        if ref_id not in self.transitive_deps(phase.id):
            raise ValueError(
                f"Phase '{phase.id}': history references '{ref_id}' which is not in its dependency closure. "
                "History can only reference phases that are guaranteed to finish before this phase."
            )

    def _validate_history(self):
        """Validate history references at parse time."""
        foreach_phase_ids: set[str] = set()
        phase_ids = {p.id for p in self.phases}

        for phase in self.phases:
            history = getattr(phase, "history", None)
            if history:
                if not isinstance(phase, PromptPhase):
                    raise ValueError(f"Phase '{phase.id}': history is only valid on prompt phases")
                for ref_id in history:
                    self._validate_history_reference(phase, ref_id, phase_ids, foreach_phase_ids)

            if phase.foreach:
                foreach_phase_ids.add(phase.id)
