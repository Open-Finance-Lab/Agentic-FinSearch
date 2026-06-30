"""Single source of truth for the read-only data-tool allow-list.

Imported by BOTH ``planner.skills.web_research`` (the fallback skill) and the
planner-failure fallback plan in ``datascraper.datascraper`` so the two former
``tools_allowed=None`` bypasses can never drift apart. Every name here is a
REAL, registered tool name -- 9 yahoo-finance + 7 tradingview + 21 sec-edgar +
3 xbrl-taxonomy + 6 in-process @function_tool callables = 46. No filesystem
tools, no report_claim (added after the allow-list filter), and NOT the
fictional ``search_filings`` advertised in prompts/core.md.
"""

READ_ONLY_DATA_TOOLS = [
    # yahoo-finance (9)
    "get_stock_info",
    "get_stock_financials",
    "get_stock_news",
    "get_stock_history",
    "get_stock_analysis",
    "get_earnings_info",
    "get_options_chain",
    "get_options_summary",
    "get_holders",
    # tradingview (7)
    "get_coin_analysis",
    "get_top_gainers",
    "get_top_losers",
    "get_bollinger_scan",
    "get_rating_filter",
    "get_consecutive_candles",
    "get_advanced_candle_pattern",
    # sec-edgar (21)
    "get_cik_by_ticker",
    "get_company_info",
    "search_companies",
    "get_company_facts",
    "get_recent_filings",
    "get_filing_content",
    "analyze_8k",
    "get_filing_sections",
    "get_financials",
    "get_segment_data",
    "get_key_metrics",
    "compare_periods",
    "discover_company_metrics",
    "get_xbrl_concepts",
    "discover_xbrl_concepts",
    "get_insider_transactions",
    "get_insider_summary",
    "get_form4_details",
    "analyze_form4_transactions",
    "analyze_insider_sentiment",
    "get_recommended_tools",
    # xbrl-taxonomy (3)
    "lookup_xbrl_tags",
    "validate_xbrl_tag",
    "query_xbrl_filing",
    # in-process @function_tool callables (6)
    "resolve_url",
    "scrape_url",
    "navigate_to_url",
    "click_element",
    "extract_page_content",
    "calculate",
]
