from __future__ import annotations

import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "services" / "api" / "app"
FAILURES: list[str] = []

# Existing compatibility debt is frozen here and removed as domains are extracted.
ROUTE_ALLOWLIST = {
    "application.py",
    "agent_v2/router.py",
    "campaign/router.py",
    "research/router.py",
    "routers/system.py",
    "routers/drafts.py",
    "routers/workspace.py",
    "routers/atlas.py",
    "routers/agent_v2.py",
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
for path in sorted(APP.rglob("*.py")):
    rel = relative(path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
    except SyntaxError as error:
        FAILURES.append(f"{rel}:{error.lineno}: cannot parse Python source")
        continue

    modules = imported_modules(tree)
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

if application_route_count > 55:
    FAILURES.append(
        f"application.py: route count grew from the Workspace extraction baseline of 55 to {application_route_count}"
    )

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

if FAILURES:
    print("\n".join(FAILURES), file=sys.stderr)
    raise SystemExit(1)

print("Python dependency boundaries passed.")
