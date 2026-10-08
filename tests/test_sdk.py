from harness.sdk import READ_TOOLS, check_billing, render_prompt, worker_options


def test_worker_isolated_from_user_settings_and_account_connectors():
    opts = worker_options(cwd="/tmp/x", model="haiku", tools=READ_TOOLS)
    assert opts.setting_sources == ["project"]
    assert opts.strict_mcp_config is True
    assert opts.mcp_servers == {}


def test_subscription_rejects_api_key():
    assert "ANTHROPIC_API_KEY" in check_billing("subscription", {"ANTHROPIC_API_KEY": "sk-x"})


def test_subscription_ok_without_key():
    assert check_billing("subscription", {"PATH": "x"}) is None


def test_api_requires_key():
    assert check_billing("api", {}) == "billing=api but ANTHROPIC_API_KEY is not set"
    assert check_billing("api", {"ANTHROPIC_API_KEY": "sk"}) is None


def test_render_prompt_with_braces_in_values():
    text = render_prompt("implement.md", title="T", description="D", plan="P",
                         failure="Previous attempt failed:\n{weird} output")
    assert "T" in text and "{weird} output" in text


def test_render_plan_prompt():
    text = render_prompt("plan.md", title="Lazy loading", description="Popis", clarifications="")
    assert "Lazy loading" in text and "UNCLEAR:" in text


def test_plan_prompt_carries_owner_answers():
    from harness.nodes.real import clarification_block
    assert clarification_block([]) == ""
    block = clarification_block(["Q?", "měř LCP"])
    text = render_prompt("plan.md", title="T", description="D", clarifications=block)
    assert "- Q?\n- měř LCP" in text and "owner" in text
