"""Auto-discovers workflow plugins in workflows/*.py.

Each module must expose `WORKFLOW` (a Workflow subclass or instance). Files
starting with "_" are skipped. A module that fails to import or is invalid is
reported in `errors` (and on /health) instead of taking the server down, so
one student's broken workflow never blocks the other four.
"""
import importlib
import pkgutil

import workflows as workflows_pkg
from core.workflow import NAME_RE, Workflow

REQUIRED_METHODS = ("validate_input", "planner_prompt", "fallback_plan", "work",
                    "reviewer_prompt", "fallback_review")


class Registry:
    def __init__(self, package=workflows_pkg):
        self.package = package
        self.workflows: dict[str, Workflow] = {}
        self.errors: dict[str, str] = {}
        self.discover()

    def discover(self) -> None:
        self.workflows.clear()
        self.errors.clear()
        for module_info in sorted(pkgutil.iter_modules(self.package.__path__), key=lambda m: m.name):
            if module_info.name.startswith("_"):
                continue
            module_name = f"{self.package.__name__}.{module_info.name}"
            try:
                module = importlib.import_module(module_name)
                workflow = self._instantiate(getattr(module, "WORKFLOW", None))
                self._check(workflow)
            except Exception as exc:
                self.errors[module_info.name] = f"{type(exc).__name__}: {exc}"
                continue
            if workflow.name in self.workflows:
                self.errors[module_info.name] = f"duplicate workflow name '{workflow.name}'"
                continue
            self.workflows[workflow.name] = workflow

    @staticmethod
    def _instantiate(candidate) -> Workflow:
        if candidate is None:
            raise ValueError("module does not define WORKFLOW")
        if isinstance(candidate, type) and issubclass(candidate, Workflow):
            return candidate()
        if isinstance(candidate, Workflow):
            return candidate
        raise TypeError("WORKFLOW must be a Workflow subclass or instance")

    @staticmethod
    def _check(workflow: Workflow) -> None:
        if not NAME_RE.match(workflow.name or ""):
            raise ValueError(f"name '{workflow.name}' must be snake_case with a feature prefix, e.g. budget_audit")
        if not isinstance(workflow.allowed_tools, (list, tuple)):
            raise ValueError("allowed_tools must be a list of MCP tool names")
        for method in REQUIRED_METHODS:
            if getattr(type(workflow), method) is getattr(Workflow, method):
                raise ValueError(f"workflow does not implement {method}()")

    def get(self, name: str) -> Workflow | None:
        return self.workflows.get(name)

    def names(self) -> list[str]:
        return sorted(self.workflows)

    def describe(self) -> list[dict]:
        return [self.workflows[name].describe() for name in self.names()]
