"""
Project templates registry for artifact propagation.

Defines templates that declare per-role artifact keys, paths, upstream roles,
and artifact caps. Used to register artifacts and expand role-level dependency
edges to artifact-level edges.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ArtifactSpec:
    """Specification for a single artifact in a template."""
    key: str                    # e.g., "BA-REQ"
    rel_path: str               # e.g., "Requirements/BA-REQ.md"
    role: str                   # e.g., "Business Analyst"
    upstream_roles: list[str] = field(default_factory=list)  # Role names this artifact depends on
    cap: int = 10               # Max artifacts of this type per role (D18)


@dataclass
class ProjectTemplate:
    """Project template defining artifact structure and dependencies."""
    id: str                     # Template identifier (e.g., "sdlc")
    name: str                   # Human-readable name
    description: str
    artifacts: list[ArtifactSpec]
    role_to_persona: dict[str, str] = field(default_factory=dict)  # Role -> Persona name
    default_propagation_mode: str = "propose"


# ── Template Registry ──────────────────────────────────────────────────────────

PROJECT_TEMPLATES = {
    "sdlc": ProjectTemplate(
        id="sdlc",
        name="SDLC Project",
        description="Standard software development lifecycle with BA, UX, DB, QA, SEC, PM roles",
        artifacts=[
            # Business Analyst artifacts
            ArtifactSpec(
                key="BA-REQ",
                rel_path="Requirements/BA-REQ.md",
                role="Business Analyst",
                upstream_roles=[],
                cap=10,
            ),
            # UX Designer artifacts
            ArtifactSpec(
                key="UX-WIRE",
                rel_path="Design/UX-WIRE.md",
                role="UX Designer",
                upstream_roles=["Business Analyst"],
                cap=10,
            ),
            # Database Developer artifacts
            ArtifactSpec(
                key="DB-MODEL",
                rel_path="Data/DB-MODEL.md",
                role="Database Developer",
                upstream_roles=["Business Analyst"],
                cap=10,
            ),
            # QA/Tester artifacts
            ArtifactSpec(
                key="QA-PLAN",
                rel_path="Test Cases/QA-PLAN.md",
                role="QA/Tester",
                upstream_roles=["Business Analyst", "Database Developer"],
                cap=10,
            ),
            # DevSecOps/Security Analyst artifacts
            ArtifactSpec(
                key="SEC-RISK",
                rel_path="Security/SEC-RISK.md",
                role="Security Analyst",
                upstream_roles=["Business Analyst"],
                cap=10,
            ),
            # Project Manager artifacts
            ArtifactSpec(
                key="PM-PLAN",
                rel_path="Project Plan/PM-PLAN.md",
                role="Project Manager",
                upstream_roles=["UX Designer", "Database Developer", "QA/Tester", "Security Analyst"],
                cap=10,
            ),
        ],
        role_to_persona={
            "Business Analyst": "Business Analyst",
            "UX Designer": "UX Designer",
            "Database Developer": "Database Developer",
            "QA/Tester": "QA/Tester",
            "Security Analyst": "Security Analyst",
            "Project Manager": "Project Manager",
        },
        default_propagation_mode="propose",
    ),
}


def get_template(template_id: str) -> Optional[ProjectTemplate]:
    """Get a template by ID."""
    return PROJECT_TEMPLATES.get(template_id)


def list_templates() -> list[ProjectTemplate]:
    """List all available templates."""
    return list(PROJECT_TEMPLATES.values())


# ── Role-to-artifact expansion ──────────────────────────────────────────────────

def expand_role_edges_to_artifact_edges(template: ProjectTemplate) -> list[tuple[str, str]]:
    """
    Expand role-level dependency edges to artifact-level edges.

    For each artifact, find all artifacts in upstream roles and create
    (upstream_key, downstream_key) pairs.

    Args:
        template: The project template.

    Returns:
        List of (upstream_artifact_key, downstream_artifact_key) tuples.
    """
    # Build role -> artifact keys mapping
    role_to_keys = {}
    for artifact in template.artifacts:
        if artifact.role not in role_to_keys:
            role_to_keys[artifact.role] = []
        role_to_keys[artifact.role].append(artifact.key)

    # Expand edges
    edges = []
    for artifact in template.artifacts:
        for upstream_role in artifact.upstream_roles:
            upstream_keys = role_to_keys.get(upstream_role, [])
            for upstream_key in upstream_keys:
                edges.append((upstream_key, artifact.key))

    return edges


def get_artifact_spec(template: ProjectTemplate, artifact_key: str) -> Optional[ArtifactSpec]:
    """Get artifact specification by key."""
    for artifact in template.artifacts:
        if artifact.key == artifact_key:
            return artifact
    return None


def get_artifacts_by_role(template: ProjectTemplate, role: str) -> list[ArtifactSpec]:
    """Get all artifact specs for a given role."""
    return [a for a in template.artifacts if a.role == role]


# ── Topological sort with cycle detection ───────────────────────────────────────

class CycleDetectedError(Exception):
    """Raised when a cycle is detected in the dependency graph."""
    def __init__(self, cycle: list[str]):
        self.cycle = cycle
        super().__init__(f"Cycle detected in dependency graph: {' -> '.join(cycle)}")


def topological_sort(artifact_keys: list[str], edges: list[tuple[str, str]]) -> list[str]:
    """
    Topological sort of artifacts by dependency edges.

    Args:
        artifact_keys: List of all artifact keys to sort.
        edges: List of (upstream, downstream) dependency edges.

    Returns:
        Sorted list of artifact keys.

    Raises:
        CycleDetectedError: If a cycle is detected.
    """
    # Build adjacency list and in-degree count
    adj = {key: [] for key in artifact_keys}
    in_degree = {key: 0 for key in artifact_keys}

    for upstream, downstream in edges:
        if upstream in adj and downstream in adj:
            adj[upstream].append(downstream)
            in_degree[downstream] += 1

    # Kahn's algorithm with cycle detection
    queue = [key for key in artifact_keys if in_degree[key] == 0]
    # Sort for deterministic ordering
    queue.sort()

    result = []
    while queue:
        current = queue.pop(0)
        result.append(current)

        for neighbor in sorted(adj[current]):  # Deterministic ordering
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)
        queue.sort()  # Maintain deterministic order

    if len(result) != len(artifact_keys):
        # Cycle detected - find it for error message
        remaining = set(artifact_keys) - set(result)
        cycle = _find_cycle(remaining, edges)
        raise CycleDetectedError(cycle)

    return result


def _find_cycle(nodes: set[str], edges: list[tuple[str, str]]) -> list[str]:
    """Find a cycle in the remaining nodes for error reporting."""
    # Simple DFS to find a cycle
    adj = {n: [] for n in nodes}
    for u, v in edges:
        if u in adj and v in adj:
            adj[u].append(v)

    visited = set()
    path = []

    def dfs(node):
        visited.add(node)
        path.append(node)
        for neighbor in adj[node]:
            if neighbor not in visited:
                result = dfs(neighbor)
                if result:
                    return result
            elif neighbor in path:
                # Found cycle
                idx = path.index(neighbor)
                return path[idx:] + [neighbor]
        path.pop()
        return None

    for node in nodes:
        if node not in visited:
            cycle = dfs(node)
            if cycle:
                return cycle

    return list(nodes)[:3]  # Fallback


def compute_depths(artifact_keys: list[str], edges: list[tuple[str, str]]) -> dict[str, int]:
    """
    Compute depth (longest path from source) for each artifact.

    Args:
        artifact_keys: List of all artifact keys.
        edges: List of (upstream, downstream) dependency edges.

    Returns:
        Dict mapping artifact_key -> depth (0 for sources/no upstream deps).
    """
    # Build REVERSE adjacency list (downstream -> upstream)
    rev_adj = {key: [] for key in artifact_keys}
    for upstream, downstream in edges:
        if upstream in rev_adj and downstream in rev_adj:
            rev_adj[downstream].append(upstream)

    # Compute depths using DFS with memoization
    # Depth = longest path from a source (node with no upstream deps)
    depths = {}

    def get_depth(node):
        if node in depths:
            return depths[node]
        if not rev_adj[node]:
            # Source node (no upstream dependencies) has depth 0
            depths[node] = 0
            return 0
        # Depth = 1 + max depth of all upstream dependencies
        max_depth = max(get_depth(n) for n in rev_adj[node]) + 1
        depths[node] = max_depth
        return max_depth

    for key in artifact_keys:
        get_depth(key)

    return depths


# ── Artifact cap management ────────────────────────────────────────────────────

def check_artifact_cap(
    project_id: int,
    template: ProjectTemplate,
    role: str,
) -> tuple[bool, int, int]:
    """
    Check if adding an artifact for a role would exceed the cap.

    Args:
        project_id: Project (folder) ID.
        template: Project template.
        role: Role name.

    Returns:
        (allowed, current_count, cap) tuple.
    """
    # Count existing artifacts for this role in the project
    from database import Artifact, Persona, db

    persona_name = template.role_to_persona.get(role)
    if not persona_name:
        return True, 0, template.artifacts[0].cap if template.artifacts else 10

    persona = Persona.query.filter_by(name=persona_name).first()
    if not persona:
        return True, 0, template.artifacts[0].cap if template.artifacts else 10

    current = Artifact.query.filter_by(
        project_id=project_id,
        role_persona_id=persona.id,
    ).count()

    cap = template.artifacts[0].cap if template.artifacts else 10
    # Find cap for this role's artifacts
    for artifact in template.artifacts:
        if artifact.role == role:
            cap = artifact.cap
            break

    return current <= cap, current, cap