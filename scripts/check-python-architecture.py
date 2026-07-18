from __future__ import annotations

import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "services" / "api" / "app"
FAILURES: list[str] = []

# Existing compatibility debt is frozen here and removed as domains are extracted.
ROUTE_ALLOWLIST = {
    "agent_v2/router.py",
    "campaign/router.py",
    "research/router.py",
    "routers/system.py",
    "routers/drafts.py",
    "routers/workspace.py",
    "routers/atlas.py",
    "routers/agent_v2.py",
    "routers/legacy.py",
    "routers/thread_content.py",
    "routers/change_review.py",
    "routers/task_pack.py",
}
DIRECT_REPOSITORY_ROUTER_ALLOWLIST = {"research/router.py"}
APPLICATION_CONFIG_GLOBALS = {
    "THREAD_CHAT_ACTIONS",
    "RESEARCH_TEMPLATES",
    "TEMPLATE_INSTRUCTIONS",
    "NODE_LABELS",
    "EDGE_LABELS",
}


def relative(path: Path) -> str:
    return path.relative_to(APP).as_posix()


def imported_modules(tree: ast.AST) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            modules.append(f"{prefix}{node.module or ''}")
    return modules


def is_route_decorator(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    return node.func.attr in {"get", "post", "put", "patch", "delete"}


def mutable_literal(node: ast.AST) -> bool:
    if isinstance(node, (ast.Dict, ast.List, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)):
        return True
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {
        "dict", "list", "set", "defaultdict",
    }


application_route_count = 0
module_graph: dict[str, set[str]] = {}
source_trees: dict[str, ast.AST] = {}
for path in sorted(APP.rglob("*.py")):
    rel = relative(path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
    except SyntaxError as error:
        FAILURES.append(f"{rel}:{error.lineno}: cannot parse Python source")
        continue

    modules = imported_modules(tree)
    source_trees[rel] = tree
    internal_dependencies: set[str] = set()
    package_parts = list(Path(rel).parent.parts)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level <= 0:
            continue
        prefix = package_parts[: max(0, len(package_parts) - (node.level - 1))]
        module_parts = (node.module or "").split(".") if node.module else []
        candidate = "/".join(prefix + module_parts)
        module_file = APP / f"{candidate}.py"
        package_file = APP / candidate / "__init__.py"
        if module_file.exists():
            internal_dependencies.add(relative(module_file))
        elif package_file.exists():
            internal_dependencies.add(relative(package_file))
    module_graph[rel] = internal_dependencies
    route_count = sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for decorator in node.decorator_list
        if is_route_decorator(decorator)
    )
    if route_count and rel not in ROUTE_ALLOWLIST:
        FAILURES.append(f"{rel}: HTTP routes belong in registered router modules")
    if rel == "application.py":
        application_route_count = route_count
        allowed_functions = {
            "utc_now", "ensure_dirs", "reset_app_services", "reset_agent_services", "app_lifespan",
            "load_thread", "write_thread", "load_project", "write_project", "load_object_memory",
            "effective_object_memory", "write_object_memory", "load_atlas_updates", "write_atlas_updates",
            "atlas_bundle_for_update", "safe_markdown_text", "campaign_plan_model",
        }
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not (node.name in allowed_functions or node.name.startswith(("get_", "v2_"))):
                    FAILURES.append(f"{rel}:{node.lineno}: non-assembly function '{node.name}' belongs in a service")
        for node in tree.body:
            if isinstance(node, ast.Assign):
                names = [target.id for target in node.targets if isinstance(target, ast.Name)]
                value = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names = [node.target.id]
                value = node.value
            else:
                continue
            for name in names:
                if value is not None and mutable_literal(value) and name not in APPLICATION_CONFIG_GLOBALS:
                    FAILURES.append(f"{rel}:{node.lineno}: mutable runtime state '{name}' must live in AppServices")

    if rel.startswith("services/") and any(module.lstrip(".").startswith(("fastapi", "starlette")) for module in modules):
        FAILURES.append(f"{rel}: service modules must not depend on FastAPI or Starlette")

    if rel.startswith("repositories/") and any(
        "services" in module.split(".") or "routers" in module.split(".") for module in modules
    ):
        FAILURES.append(f"{rel}: repositories must not import services or routers")

    if rel.endswith("router.py") or rel.startswith("routers/"):
        direct_repository = any(
            module.endswith(".store") or "repositories" in module.split(".") for module in modules
        )
        if direct_repository and rel not in DIRECT_REPOSITORY_ROUTER_ALLOWLIST:
            FAILURES.append(f"{rel}: routers must call services instead of repositories/stores directly")

    if rel.startswith("routers/"):
        allowed_roots = ("__future__", "fastapi", "collections", "pathlib", "typing", "..schemas", "..services")
        for module in modules:
            if not module.startswith(allowed_roots):
                FAILURES.append(f"{rel}: router dependency '{module}' must go through schemas or services")

    if rel.startswith(("agent_v2/", "research/", "campaign/")) and any("legacy" in module.split(".") for module in modules):
        FAILURES.append(f"{rel}: hot-path domains must not import legacy compatibility code")

    if rel.startswith(("agent_v2/", "research/", "campaign/")):
        for node in tree.body:
            if isinstance(node, ast.Assign):
                names = [target.id for target in node.targets if isinstance(target, ast.Name)]
                value = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names = [node.target.id]
                value = node.value
            else:
                continue
            for name in names:
                if value is not None and mutable_literal(value) and not name.isupper() and not name.startswith("__"):
                    FAILURES.append(f"{rel}:{node.lineno}: mutable module state '{name}' must live in application services")

if application_route_count:
    FAILURES.append("application.py: HTTP routes must be registered from router modules")

for entry in sorted(path for path in module_graph if path.startswith(("agent_v2/", "research/", "campaign/"))):
    pending = list(module_graph[entry])
    visited: set[str] = set()
    while pending:
        dependency = pending.pop()
        if dependency in visited or dependency.startswith("schemas/"):
            continue
        visited.add(dependency)
        if dependency.startswith("legacy/"):
            FAILURES.append(f"{entry}: transitive dependency reaches {dependency}")
            break
        pending.extend(module_graph.get(dependency, ()))

application_source = (APP / "application.py").read_text(encoding="utf-8")
for forbidden_global in ("AGENT_V2_RUNTIME", "RESEARCH_STORE", "KNOWLEDGE_ENRICHMENT", "CAMPAIGN_SERVICE"):
    if forbidden_global in application_source:
        FAILURES.append(f"application.py: mutable service global {forbidden_global} must remain in AppServices")
if "@app.on_event" in application_source:
    FAILURES.append("application.py: startup and shutdown must use FastAPI lifespan")
if "create_app(lifespan=app_lifespan)" not in application_source:
    FAILURES.append("application.py: AppServices must be owned by the ASGI lifespan")
if "create_atlas_router(get_atlas_service)" not in application_source:
    FAILURES.append("application.py: Atlas router must be registered through its service boundary")
if "create_agent_v2_router(get_agent_api_service)" not in application_source:
    FAILURES.append("application.py: Agent v2 router must be registered through its service boundary")
if "create_application" not in application_source or "ApplicationConfig" not in application_source:
    FAILURES.append("application.py: isolated application construction must remain available")

if FAILURES:
    print("\n".join(FAILURES), file=sys.stderr)
    raise SystemExit(1)

print("Python dependency boundaries passed.")
