from __future__ import annotations


class SchemaReadOnlyError(RuntimeError):
    def __init__(self, *, database_schema: int, supported_schema: int):
        self.detail = {
            "code": "schema_newer_than_app",
            "message": "研究数据库由更高版本的 EAI 创建；当前版本已进入只读模式。",
            "database_schema": database_schema,
            "supported_schema": supported_schema,
            "read_only": True,
        }
        super().__init__(self.detail["message"])


class RevisionConflictError(RuntimeError):
    def __init__(self, detail: dict):
        self.detail = detail
        super().__init__(str(detail.get("message") or "revision conflict"))
