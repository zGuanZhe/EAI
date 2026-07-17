from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import uvicorn


SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

personal_dir = Path(tempfile.gettempdir()) / "eai-vnext-agent-ui-test"
if personal_dir.exists():
    shutil.rmtree(personal_dir)

os.environ["EAI_PERSONAL_DIR"] = str(personal_dir)
os.environ["OPENROUTER_API_KEY"] = "ui-test-dummy-key"
os.environ["EAI_VNEXT_MOCK_OPENAI_RESPONSE"] = """```eai-agent-run/v1
{
  "answer": "## 当前判断\\n\\n应先把 Atlas 证据与 Canvas 论证结构对齐。依据来自本轮可核查来源 [S1]。\\n\\n- 先确认论文卡中的证据边界\\n- 对缺少原文的信息标注需要原文确认",
  "action_proposals": [
    {
      "type": "object_memory",
      "target": {"atlas_id": "G", "object_type": "paper", "object_id": "ui-test-paper", "title": "UI Test Paper"},
      "summary": "补充测试论文的个人判断",
      "diff": [{"field": "judgement", "after": "这是一条经过用户确认后才写入的测试判断。", "reason": "验证可审计变更集"}],
      "risk": "low"
    }
  ]
}
```"""


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        app_dir=str(SERVICE_ROOT),
        host="127.0.0.1",
        port=int(os.environ.get("EAI_UI_TEST_PORT", "8001")),
        log_level="warning",
    )
