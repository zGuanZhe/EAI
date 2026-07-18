from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright


sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "test-results"
RESULTS.mkdir(parents=True, exist_ok=True)
BASE_URL = os.environ.get("EAI_UI_URL", "http://127.0.0.1:5173")
API_URL = os.environ.get("EAI_API_URL", "http://127.0.0.1:8001/api/vnext")


def api_post(path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{API_URL}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def api_get(path: str) -> dict:
    with urllib.request.urlopen(f"{API_URL}{path}", timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def current_agent_task_id(page: Page) -> str:
    latest = page.locator(".conversation-turn.agent-turn").last
    return latest.get_attribute("data-agent-task-id") if latest.count() else ""


def wait_for_current_agent(page: Page) -> None:
    page.locator(".conversation-turn.agent-turn").last.wait_for(state="visible", timeout=20_000)
    try:
        page.wait_for_function(
            """() => {
              const turns = document.querySelectorAll('.conversation-turn.agent-turn');
              const latest = turns[turns.length - 1];
              return latest && !['pending', 'streaming'].includes(latest.dataset.messageStatus);
            }""",
            timeout=30_000,
        )
    except Exception:
        latest = page.locator(".conversation-turn.agent-turn").last
        diagnostic = {
            "task_id": latest.get_attribute("data-agent-task-id"),
            "message_status": latest.get_attribute("data-message-status"),
            "text": latest.inner_text()[:1200],
        }
        print(json.dumps({"agent_wait_timeout": diagnostic}, ensure_ascii=False), file=sys.stderr)
        raise


def wait_for_new_agent(page: Page, previous_task_id: str = "") -> None:
    page.wait_for_function(
        """previousTaskId => {
          const turns = document.querySelectorAll('.conversation-turn.agent-turn');
          const latest = turns[turns.length - 1];
          return latest && latest.dataset.agentTaskId && latest.dataset.agentTaskId !== previousTaskId;
        }""",
        arg=previous_task_id,
        timeout=20_000,
    )
    wait_for_current_agent(page)


def assert_no_horizontal_overflow(page: Page) -> None:
    metrics = page.evaluate("() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth })")
    assert metrics["scrollWidth"] <= metrics["width"], metrics


created_thread = api_post(
    "/threads",
    {
        "title": "主路径网页验收",
        "goal": "验证 Main Agent、上下文和 Atlas 的核心体验。",
        "active_atlas_id": "I",
    },
)
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    console_errors: list[str] = []
    page_errors: list[str] = []
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.goto(BASE_URL, wait_until="networkidle")

    page.locator(".home-surface").wait_for(state="visible")
    home_title_size = page.locator(".home-surface h1").evaluate("node => parseFloat(getComputedStyle(node).fontSize)")
    assert home_title_size >= 38, home_title_size
    home_main_box = page.locator(".home-main").bounding_box()
    home_footer_box = page.locator(".home-composer-footer").bounding_box()
    assert abs((home_footer_box["y"] + home_footer_box["height"]) - (home_main_box["y"] + home_main_box["height"])) <= 2
    assert page.locator(".home-composer-footer .composer").evaluate("node => parseFloat(getComputedStyle(node).borderRadius)") >= 16
    expanded_sidebar_width = page.locator(".sidebar").bounding_box()["width"]
    page.locator(".sidebar-icon-button.end").click()
    page.wait_for_timeout(260)
    assert page.locator(".sidebar").bounding_box()["width"] <= 66
    page.screenshot(path=str(RESULTS / "home-sidebar-collapsed.png"))
    page.locator(".sidebar-brand-glyph").click()
    page.wait_for_timeout(260)
    assert abs(page.locator(".sidebar").bounding_box()["width"] - expanded_sidebar_width) <= 2
    page.screenshot(path=str(RESULTS / "home-desktop.png"))
    page.locator(".sidebar-footer button").last.click()
    page.locator(".settings-drawer").wait_for(state="visible")
    page.screenshot(path=str(RESULTS / "settings-desktop.png"))
    page.keyboard.press("Escape")
    page.locator(".settings-drawer").wait_for(state="detached")

    page.locator(".surface-nav-item.blue").click()
    composer = page.locator(".composer textarea")
    composer.wait_for(state="visible")
    mode_select = page.locator(".composer-mode-select select")
    assert mode_select.input_value() == "auto"
    composer_box = composer.bounding_box()
    mode_box = mode_select.bounding_box()
    assert composer_box and mode_box
    assert mode_box["x"] >= composer_box["x"] + composer_box["width"]
    assert float(mode_select.evaluate("node => getComputedStyle(node).borderRadius.replace('px', '')")) >= 16
    page.locator(".sidebar-footer button").last.click()
    page.locator(".settings-drawer").wait_for(state="visible")
    page.locator(".ui-drawer-backdrop").click(position={"x": 8, "y": 8})
    page.locator(".settings-drawer").wait_for(state="detached")

    composer.fill("你好")
    composer.press("Enter")
    wait_for_new_agent(page)
    greeting = page.locator(".conversation-turn.agent-turn").last
    assert "Agent Runtime v2 natural response." in greeting.inner_text()
    assert greeting.locator(".agent-run-trace").count() == 0
    assert greeting.locator(".agent-citations").count() == 0
    assert "个 Skill" not in greeting.inner_text()

    page.locator(".composer-context-chip").click()
    drawer = page.locator(".context-drawer")
    drawer.wait_for(state="visible")
    assert page.locator(".right-rail:not(.collapsed)").count() == 0
    assert "tokens" not in drawer.inner_text().lower()
    assert drawer.get_by_role("button", name="去 Atlas 选择").count() == 1
    page.screenshot(path=str(RESULTS / "context-drawer-desktop.png"))
    drawer.get_by_role("button", name="去 Atlas 选择").click()

    atlas = page.locator(".atlas-surface")
    atlas.wait_for(state="visible")
    page.locator(".sidebar-footer button").last.click()
    page.locator(".settings-drawer").wait_for(state="visible")
    page.locator(".settings-drawer .ui-drawer-header button").click()
    page.locator(".settings-drawer").wait_for(state="detached")
    assert page.locator(".composer").count() == 0
    page.locator(".atlas-route-navigator").wait_for(state="visible")
    assert "路线" in page.locator(".atlas-route-navigator").inner_text()
    assert page.locator(".atlas-filter-fields select").count() == 2
    assert "43 篇正式" in atlas.inner_text()
    assert page.locator(".paper-evidence-dot").count() == page.locator(".timeline-paper").count()
    page.screenshot(path=str(RESULTS / "atlas-default-desktop.png"))

    main_width = page.locator(".main").bounding_box()["width"]
    rightmost_index = page.locator(".timeline-paper").evaluate_all(
        "nodes => nodes.reduce((best, node, index) => node.getBoundingClientRect().right > nodes[best].getBoundingClientRect().right ? index : best, 0)"
    )
    paper = page.locator(".timeline-paper").nth(rightmost_index)
    paper.click()
    page.locator(".right-rail:not(.collapsed)").wait_for(state="visible")
    page.wait_for_timeout(280)
    pushed_main_width = page.locator(".main").bounding_box()["width"]
    assert main_width - pushed_main_width >= 318, (main_width, pushed_main_width)
    visibility = page.evaluate(
        """() => {
          const paper = document.querySelector('.timeline-paper.selected').getBoundingClientRect();
          const shell = document.querySelector('.timeline-shell').getBoundingClientRect();
          const rail = document.querySelector('.right-rail').getBoundingClientRect();
          return { paperRight: paper.right, safeRight: Math.min(shell.right, rail.left) };
        }"""
    )
    assert visibility["paperRight"] <= visibility["safeRight"] - 20, visibility
    assert page.locator(".relation-label").count() == 0
    if page.locator(".timeline-edge-hit").count():
        edge = page.locator(".timeline-edge-hit").first
        edge.hover()
        assert page.locator(".relation-label").count() == 1
        edge.click()
        page.mouse.move(20, 20)
        page.wait_for_function(
            "document.querySelectorAll('.atlas-relation.locked .relation-label').length === 1",
        )
        assert page.locator(".relation-label").count() == 1
    page.screenshot(path=str(RESULTS / "atlas-focused-desktop.png"))
    page.locator(".rail-close").click()
    page.wait_for_timeout(280)
    assert abs(page.locator(".main").bounding_box()["width"] - main_width) <= 2

    page.locator(".timeline-paper").first.dblclick()
    reader = page.locator(".paper-reader")
    reader.wait_for(state="visible")
    assert page.locator(".right-rail").count() == 0
    assert reader.locator("textarea").count() == 0
    assert reader.locator(".paper-pending-fields").count() == 1
    reader.locator(".paper-evidence").wait_for(state="visible", timeout=10_000)
    assert "结构化论断与可核查来源" in reader.inner_text()
    assert reader.locator(".paper-claim").count() >= 1
    reader.locator(".paper-claim").first.locator("summary").click()
    assert reader.locator(".paper-claim blockquote").count() >= 1
    page.screenshot(path=str(RESULTS / "paper-reader-desktop.png"))
    reader.locator(".paper-reader-back").click()

    paper = page.locator(".timeline-paper").first
    paper.click()
    page.locator(".timeline-paper.selected .paper-node-actions button").first.click()
    page.locator(".agent-thread-surface").wait_for(state="visible")
    page.locator(".composer-attachments").wait_for(state="visible")
    assert page.locator(".composer-attachments > span").count() == 1

    page.locator(".composer-context-chip").click()
    page.locator(".context-drawer").wait_for(state="visible")
    assert page.locator(".context-material-row:not(.long-term)").count() == 1
    page.locator(".context-drawer .ui-drawer-header button").click()

    user_count = page.locator(".conversation-turn.user-turn").count()
    page.locator(".composer-mode-select select").select_option("local")
    previous_task_id = current_agent_task_id(page)
    composer.fill("请基于当前 Atlas 检索最相关的论文，并说明下一步。")
    composer.press("Enter")
    page.locator(".composer-run-actions .send-button.stop").wait_for(state="visible", timeout=10_000)
    assert page.locator(".composer-run-actions .send-button").count() == 2
    assert "追加要求" in composer.get_attribute("placeholder")
    composer.fill("追加要求：只关注能定位到原文或明确证据等级的材料。")
    composer.press("Enter")
    page.get_by_text("追加要求：只关注能定位到原文或明确证据等级的材料。", exact=True).wait_for(state="visible")
    wait_for_new_agent(page, previous_task_id)
    assistant = page.locator(".conversation-turn.agent-turn").last
    assert "Agent Runtime v2 natural response." in assistant.inner_text()
    citation_count = assistant.locator(".agent-citations").count()
    if citation_count != 1:
        latest_thread = api_get(f"/threads/{created_thread['id']}")
        raise AssertionError(
            json.dumps(
                {
                    "citation_count": citation_count,
                    "assistant_text": assistant.inner_text(),
                    "saved_refs": latest_thread["messages"][-1].get("refs", {}),
                },
                ensure_ascii=False,
            )
        )
    assert assistant.locator(".agent-v2-activity").count() == 1
    assert "证据研究已完成" in assistant.inner_text()
    assert assistant.locator(".agent-run-trace").count() == 0
    assert page.locator(".composer-attachments").count() == 0

    assistant.locator(".agent-v2-audit summary").click()
    assistant.locator(".agent-v2-audit-call").first.wait_for(state="visible")
    audit = api_get(f"/agent-v2/tasks/{current_agent_task_id(page)}/audit")
    assert "steer_applied" in [event["kind"] for event in audit["events"]]

    assistant.locator(".agent-citations summary").click()
    assistant.locator(".agent-citations button").first.click()
    page.locator(".right-rail .agent-source-detail").wait_for(state="visible")
    assert "证据边界" in page.locator(".right-rail .agent-source-detail").inner_text()
    page.locator(".rail-close").click()

    assistant.locator(".assistant-actions button").first.click()
    wait_for_current_agent(page)
    assert page.locator(".conversation-turn.user-turn").count() == user_count + 2

    page.locator(".surface-nav-item.cyan").click()
    page.locator(".atlas-surface").wait_for(state="visible")
    approval_paper = page.locator(".timeline-paper").first
    approval_paper.click()
    page.locator(".timeline-paper.selected .paper-node-actions button").first.click()
    page.locator(".agent-thread-surface").wait_for(state="visible")
    page.locator(".composer-mode-select select").select_option("auto")
    composer = page.locator(".composer textarea")
    composer.fill("把这篇论文保存为长期资料")
    composer.press("Enter")
    approval_panel = page.locator(".conversation-turn.agent-turn .agent-v2-approval").last
    approval_panel.wait_for(state="visible", timeout=20_000)
    assert page.locator(".composer-run-actions .send-button.stop").count() == 0
    approval_panel.get_by_role("button", name="检查并确认").click()
    approval_inspector = page.locator(".right-rail .agent-approval-inspector")
    approval_inspector.wait_for(state="visible")
    assert "权限边界" in approval_inspector.inner_text()
    assert approval_inspector.locator(".approval-field-diff").count() >= 1
    approval_inspector.get_by_role("button", name="确认所选修改").click()
    wait_for_current_agent(page)
    page.locator(".composer-context-chip").click()
    page.locator(".context-drawer").wait_for(state="visible")
    assert page.locator(".context-material-row.long-term").count() >= 1
    page.locator(".context-drawer .ui-drawer-header button").click()
    page.screenshot(path=str(RESULTS / "main-agent-desktop.png"))
    assert_no_horizontal_overflow(page)
    operation_receipt = page.locator(".conversation-turn.agent-turn .agent-v2-operation-receipt").last
    operation_receipt.get_by_role("button", name="撤销").click()
    page.wait_for_function("() => document.querySelector('.conversation-turn.agent-turn:last-of-type .agent-v2-operation-receipt')?.textContent.includes('已撤销')", timeout=10_000)
    page.locator(".composer-context-chip").click()
    page.locator(".context-drawer").wait_for(state="visible")
    assert page.locator(".context-material-row.long-term").count() == 0
    page.locator(".context-drawer .ui-drawer-header button").click()

    page.locator(".surface-nav-item.violet").click()
    page.locator(".vertical-canvas").wait_for(state="visible")
    assert page.locator(".composer").count() == 0
    assert page.locator(".argument-node-row").count() >= 1
    page.screenshot(path=str(RESULTS / "canvas-vertical-desktop.png"))

    page.get_by_role("button", name="Campaign", exact=True).click()
    page.locator(".campaign-empty").wait_for(state="visible")
    page.get_by_role("button", name="生成研究想法").click()
    idea_preview = page.locator(".campaign-idea-preview")
    idea_preview.wait_for(state="visible", timeout=20_000)
    assert idea_preview.locator(".campaign-idea-list article").count() == 3
    page.screenshot(path=str(RESULTS / "canvas-campaign-ideas.png"))
    idea_preview.get_by_role("button", name="选择并创建 Campaign").first.click()
    campaign_workspace = page.locator(".campaign-workspace")
    campaign_workspace.wait_for(state="visible")
    campaign_workspace.get_by_role("button", name="启动").click()
    campaign_workspace.get_by_role("button", name="实验树", exact=True).click()
    page.locator(".campaign-branch").first.wait_for(state="visible")
    assert page.locator(".campaign-branch").count() == 3
    page.locator(".campaign-branch").first.click()
    branch_drawer = page.locator(".campaign-branch-drawer")
    branch_drawer.wait_for(state="visible")
    assert branch_drawer.bounding_box()["width"] >= 320
    assert branch_drawer.get_by_role("button", name="准备分支会话").count() == 1
    page.screenshot(path=str(RESULTS / "canvas-campaign-branch.png"))
    branch_drawer.locator(".ui-drawer-header button").click()
    campaign_workspace.get_by_role("button", name="产物", exact=True).click()
    assert "还没有 Campaign 产物" in campaign_workspace.inner_text()
    campaign_workspace.get_by_role("button", name="论文", exact=True).click()
    assert campaign_workspace.get_by_role("button", name="生成论文候选稿").count() == 1
    campaign_workspace.get_by_role("button", name="审稿", exact=True).click()
    assert "还没有可审稿论文" in campaign_workspace.inner_text()
    campaign_workspace.get_by_role("button", name="总览", exact=True).click()

    page.locator(".sidebar-footer button").first.click()
    page.locator(".run-center").wait_for(state="visible")
    assert page.locator(".tools-page").count() == 0
    knowledge_section = page.locator(".knowledge-status-section")
    assert "686 篇论文" in knowledge_section.inner_text()
    assert "1556 条关系" in knowledge_section.inner_text()
    assert knowledge_section.get_by_role("button", name="核验元数据").count() == 1
    assert knowledge_section.get_by_role("button", name="补全热集全文").count() == 1
    page.screenshot(path=str(RESULTS / "run-center-desktop.png"))
    assert page.locator(".lab-workspace").count() == 0

    medium = browser.new_page(viewport={"width": 1180, "height": 820})
    medium.goto(BASE_URL, wait_until="networkidle")
    medium.locator(".surface-nav-item.cyan").click()
    medium.locator(".atlas-surface").wait_for(state="visible")
    assert medium.locator(".atlas-route-navigator").is_visible()
    medium_main_width = medium.locator(".main").bounding_box()["width"]
    medium.locator(".timeline-paper").first.click()
    medium.locator(".right-rail").wait_for(state="visible")
    medium.wait_for_timeout(280)
    assert medium_main_width - medium.locator(".main").bounding_box()["width"] >= 318
    medium.locator(".rail-close").click()
    assert_no_horizontal_overflow(medium)
    medium.screenshot(path=str(RESULTS / "atlas-medium.png"))

    narrow = browser.new_page(viewport={"width": 760, "height": 900})
    narrow.goto(BASE_URL, wait_until="networkidle")
    narrow.wait_for_timeout(300)
    sidebar_box = narrow.locator(".sidebar").bounding_box()
    main_box = narrow.locator(".main").bounding_box()
    assert sidebar_box["width"] <= 66, sidebar_box
    assert main_box["width"] >= 690, main_box
    narrow_home_title_size = narrow.locator(".home-surface h1").evaluate("node => parseFloat(getComputedStyle(node).fontSize)")
    assert narrow_home_title_size >= 29, narrow_home_title_size
    narrow_footer = narrow.locator(".home-composer-footer").bounding_box()
    assert abs((narrow_footer["y"] + narrow_footer["height"]) - 900) <= 2
    narrow.screenshot(path=str(RESULTS / "home-narrow.png"))
    narrow.locator(".sidebar-footer button").last.click()
    narrow.locator(".settings-drawer").wait_for(state="visible")
    assert_no_horizontal_overflow(narrow)
    narrow.screenshot(path=str(RESULTS / "settings-narrow.png"))
    narrow.locator(".settings-drawer .ui-drawer-header button").click()
    narrow.locator(".surface-nav-item.blue").click()
    narrow_composer = narrow.locator(".composer textarea")
    narrow_composer.fill("检索本地 VLA 证据并说明证据等级")
    narrow_composer.press("Enter")
    narrow.locator(".composer-run-actions .send-button.stop").wait_for(state="visible", timeout=10_000)
    assert narrow.locator(".composer-run-actions .send-button").count() == 2
    assert_no_horizontal_overflow(narrow)
    narrow.locator(".composer-run-actions .send-button.stop").click()
    narrow.locator(".composer-run-actions .send-button.stop").wait_for(state="detached", timeout=10_000)
    narrow.locator(".surface-nav-item.cyan").click()
    narrow.locator(".atlas-surface").wait_for(state="visible")
    narrow.locator(".atlas-filter-toggle").click()
    narrow.locator(".atlas-filter-fields.open").wait_for(state="visible")
    assert narrow.locator(".atlas-filter-fields.open select").count() == 2
    fully_visible_cards = narrow.evaluate(
        """() => [...document.querySelectorAll('.timeline-paper')].filter((node) => {
          const rect = node.getBoundingClientRect();
          const main = document.querySelector('.main').getBoundingClientRect();
          return rect.left >= main.left && rect.right <= innerWidth && rect.top >= 150 && rect.bottom <= innerHeight;
        }).length"""
    )
    assert fully_visible_cards >= 1, fully_visible_cards
    narrow_main_before_rail = narrow.locator(".main").bounding_box()["width"]
    narrow.locator(".timeline-paper").first.click()
    narrow.locator(".right-rail").wait_for(state="visible")
    narrow.wait_for_timeout(280)
    assert abs(narrow.locator(".main").bounding_box()["width"] - narrow_main_before_rail) <= 2
    narrow_visibility = narrow.evaluate(
        """() => {
          const paper = document.querySelector('.timeline-paper.selected').getBoundingClientRect();
          const rail = document.querySelector('.right-rail').getBoundingClientRect();
          return { paperRight: paper.right, railLeft: rail.left };
        }"""
    )
    assert narrow_visibility["paperRight"] <= narrow_visibility["railLeft"] - 20, narrow_visibility
    narrow.locator(".rail-close").click()
    assert_no_horizontal_overflow(narrow)
    narrow.screenshot(path=str(RESULTS / "atlas-narrow.png"))

    narrow.locator(".surface-nav-item.violet").click()
    narrow.locator(".vertical-canvas").wait_for(state="visible")
    assert_no_horizontal_overflow(narrow)
    narrow.screenshot(path=str(RESULTS / "canvas-vertical-narrow.png"))
    narrow.get_by_role("button", name="Campaign", exact=True).click()
    narrow.locator(".campaign-workspace").wait_for(state="visible")
    assert_no_horizontal_overflow(narrow)
    narrow.screenshot(path=str(RESULTS / "canvas-campaign-narrow.png"))

    narrow.locator(".sidebar-footer button").first.click()
    narrow.locator(".run-center").wait_for(state="visible")
    assert_no_horizontal_overflow(narrow)
    narrow.screenshot(path=str(RESULTS / "run-center-narrow.png"))
    assert narrow.locator(".lab-workspace").count() == 0

    reduced_context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    reduced = reduced_context.new_page()
    reduced.goto(BASE_URL, wait_until="networkidle")
    assert reduced.locator(".app").evaluate(
        "node => getComputedStyle(node).transitionDuration.split(',').every(value => parseFloat(value) === 0)"
    )
    reduced_context.close()

    assert not console_errors, console_errors
    assert not page_errors, page_errors
    print(json.dumps({"desktop_turns": page.locator(".conversation-turn").count(), "medium_width": medium.viewport_size["width"], "narrow_main_width": main_box["width"]}, ensure_ascii=False))
    browser.close()
