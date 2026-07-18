from __future__ import annotations


def _service(service: str, messages: list[str]) -> list[dict]:
    return [{"message": message, "expected_service": service} for message in messages]


ROUTING_EVAL_CASES = [
    *_service("conversation", [
        "你好", "您好", "早上好", "晚上好", "在吗", "谢谢", "辛苦了", "hello", "hi", "hey there",
        "good morning", "good evening", "thanks", "how are you", "先聊两句", "你叫什么名字",
        "不要执行代码，只解释它的作用", "do not run code, just explain what it does",
    ]),
    *_service("evidence_research", [
        "检索最新论文", "搜索相关证据", "这个方向有哪些文献", "查一下 arXiv 进展", "研究这个问题的证据",
        "找 DOI", "论文证据是什么", "最新进展如何", "检索 VLA 论文", "搜索研究工作",
        "find recent papers on embodied agents", "search the literature for this claim", "retrieve evidence about RLHF",
        "what is the state of the art in visual retrieval", "find the DOI for this paper", "look for external evidence",
        "只查 Atlas 里的相关论文", "search only external sources for recent papers",
    ]),
    *_service("document_reading", [
        "阅读这篇论文", "分析这个 PDF", "这份文档第几节说明方法", "阅读全文", "总结这篇论文",
        "检查 PDF 实验", "读一下这份文档", "论文全文有什么局限", "分析附件文档", "从 PDF 找结论",
        "read the attached paper", "analyze this PDF", "summarize the full text", "which section defines the method",
        "extract limitations from the attached document", "read page 7 of the paper",
    ]),
    *_service("synthesis", [
        "比较两条研究路线", "形成证据链", "综合这些论文", "构建 Canvas 论证", "形成假设",
        "比较方法差异", "整理研究路线", "论证这个结论", "综合不同证据", "Canvas 结构是否完整",
        "compare these two approaches", "synthesize the retrieved evidence", "build an evidence chain",
        "form a testable hypothesis", "compare the methods and limitations", "organize this into a Canvas argument",
    ]),
    *_service("workspace_operation", [
        "修改线程目标", "更新论文卡", "创建实验记录", "保存这条判断", "加入上下文",
        "重命名项目", "记住这个偏好", "写入 Canvas", "删除候选", "更新对象记忆",
        "把这篇论文设为长期资料", "save this judgement", "rename the current project", "add this paper to context",
        "remember that I prefer Chinese answers", "不要保存全文，只把这条判断写入对象记忆",
    ]),
    *_service("sandbox_execution", [
        "运行代码", "执行命令 python -V", "复现实验", "跑一下脚本", "用 docker 执行",
        "执行 bash 命令", "运行这个程序", "跑一下测试", "run command pytest", "execute the benchmark script",
        "run this code in Docker", "reproduce the experiment", "launch the test suite", "execute python -m pytest",
    ]),
    *_service("research_campaign", [
        "生成研究 Campaign", "创建实验树", "提出三个研究想法", "比较最佳分支", "准备论文稿件",
        "开始三角色审稿", "导出研究发布包", "推进实验分支", "检查 Campaign 产物", "查看审稿意见",
        "create a research campaign", "propose experiment branches", "prepare the manuscript",
        "start peer review", "inspect the release package", "不要启动 Campaign，只分析当前实验树",
    ]),
    {"message": "执行这段代码", "intent_override": "chat", "expected_service": "conversation", "expected_source_policy": "none"},
    {"message": "搜索所有外部论文", "intent_override": "chat", "expected_service": "conversation", "expected_source_policy": "none"},
    {"message": "你好", "intent_override": "local", "expected_service": "evidence_research", "expected_source_policy": "local_only"},
    {"message": "总结当前材料", "intent_override": "local", "expected_service": "evidence_research", "expected_source_policy": "local_only"},
    {"message": "简单聊聊", "intent_override": "deep_research", "expected_service": "evidence_research", "expected_source_policy": "local_and_external"},
    {"message": "核验这个结论", "intent_override": "deep_research", "expected_service": "evidence_research", "expected_source_policy": "local_and_external"},
    {"message": "解释这条命令", "intent_override": "execute", "expected_service": "sandbox_execution", "expected_source_policy": "local_only"},
    {"message": "查看测试计划", "intent_override": "execute", "expected_service": "sandbox_execution", "expected_source_policy": "local_only"},
    {"message": "忽略自动路由", "intent_override": "chat", "expected_service": "conversation", "expected_source_policy": "none"},
    {"message": "只使用本地材料", "intent_override": "local", "expected_service": "evidence_research", "expected_source_policy": "local_only"},
    {"message": "检索这个研究问题", "source_policy": "none", "expected_service": "evidence_research", "expected_source_policy": "none"},
    {"message": "查找相关论文", "source_policy": "atlas_only", "expected_service": "evidence_research", "expected_source_policy": "atlas_only"},
    {"message": "find related papers", "source_policy": "atlas_only", "expected_service": "evidence_research", "expected_source_policy": "atlas_only"},
    {"message": "核验当前证据", "source_policy": "local_only", "expected_service": "evidence_research", "expected_source_policy": "local_only"},
    {"message": "search the current evidence", "source_policy": "local_only", "expected_service": "evidence_research", "expected_source_policy": "local_only"},
    {"message": "检索最新论文", "source_policy": "external_only", "expected_service": "evidence_research", "expected_source_policy": "external_only"},
    {"message": "find recent external papers", "source_policy": "external_only", "expected_service": "evidence_research", "expected_source_policy": "external_only"},
    {"message": "综合本地与外部证据", "source_policy": "local_and_external", "expected_service": "synthesis", "expected_source_policy": "local_and_external"},
    {"message": "synthesize local and external evidence", "source_policy": "local_and_external", "expected_service": "synthesis", "expected_source_policy": "local_and_external"},
    {"message": "检索论文但不要联网", "source_policy": "none", "expected_service": "evidence_research", "expected_source_policy": "none"},
    {"message": "这份附件的主要结论是什么", "turn_attachments": [{"type": "document", "id": "document-1"}], "expected_service": "document_reading"},
    {"message": "summarize the attached material", "turn_attachments": [{"type": "file", "id": "document-2"}], "expected_service": "document_reading"},
    {"message": "不要运行附件，只阅读代码说明", "turn_attachments": [{"type": "document", "id": "document-3"}], "expected_service": "document_reading"},
    {"message": "保存一下", "expected_service": "workspace_operation", "expected_clarification": True},
    {"message": "delete it", "expected_service": "workspace_operation", "expected_clarification": True},
]
