"""Workspace e2e: the switcher isolates dashboards between workspaces. Uses a
uniquely-named workspace per run and cleans it up so reruns are idempotent."""

import uuid
from urllib.parse import quote

import httpx
from playwright.sync_api import Page, expect


def test_switcher_isolates_dashboards(page: Page, base_url: str):
    ws = f"e2e-{uuid.uuid4().hex[:6]}"
    dash = f"ws dash {ws}"
    httpx.post(f"{base_url}/api/workspaces", json={"name": ws}).raise_for_status()
    httpx.post(
        f"{base_url}/api/dashboards",
        json={
            "name": dash,
            "connection": "prod",
            "html": "<html><body>ws</body></html>",
            "queries": {},
            "workspace": ws,
        },
    ).raise_for_status()

    try:
        # Default workspace: the dashboard is absent from its list.
        page.goto(f"{base_url}/dashboard")
        expect(page.get_by_test_id("workspace-switcher")).to_contain_text("default")
        assert dash not in [
            d["name"]
            for d in httpx.get(f"{base_url}/api/dashboards").json()["dashboards"]
        ]

        # Switch: the page views remount and the dashboard resolves in that
        # workspace.
        page.get_by_test_id("workspace-switcher").click()
        page.get_by_test_id("workspace-option").filter(has_text=ws).click()
        expect(page.get_by_test_id("workspace-switcher")).to_contain_text(ws)
        page.goto(f"{base_url}/dashboard?name={quote(dash)}")
        expect(page.locator("body")).not_to_contain_text("not found")
    finally:
        # Reset the browser's persisted choice; workspace deletion is
        # best-effort (it still owns the dashboard, so the API returns 409 —
        # there is no dashboard-delete endpoint yet; see docs/future.md).
        page.evaluate("localStorage.setItem('qv_workspace', 'default')")
        httpx.request("DELETE", f"{base_url}/api/workspaces/{ws}")


def test_switcher_dismisses_on_outside_click_and_escape(page: Page, base_url: str):
    """The workspace switcher popover dismisses on an outside click and on Escape."""
    page.goto(f"{base_url}/queries")
    switcher = page.get_by_test_id("workspace-switcher")
    manage = page.get_by_test_id("workspace-manage")

    # A click well away from the top-right popover closes it.
    switcher.click()
    expect(manage).to_be_visible()
    page.mouse.click(500, 500)
    expect(manage).to_have_count(0)

    # Escape closes it too.
    switcher.click()
    expect(manage).to_be_visible()
    page.keyboard.press("Escape")
    expect(manage).to_have_count(0)
