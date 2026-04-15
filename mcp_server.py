"""
MCP Server for AI News Accident Crawler
=========================================
Model Context Protocol server that exposes crawler tools and data resources
to any MCP-compatible client (VS Code Copilot, Claude Desktop, etc.).

Tools:
  - crawl_state: Crawl a specific state for accidents
  - crawl_all: Run full India crawl
  - get_stats: Get database statistics
  - get_daily_report: Get today's accident summary
  - get_state_data: Query accident data by state
  - search_accidents: Full-text search across records
  - get_crawl_history: View past crawl runs
  - export_excel: Generate Excel for a state

Resources:
  - accident://stats — Live statistics
  - accident://today — Today's summary

Start: python mcp_server.py
"""

import asyncio
import json
from datetime import datetime

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, Resource
from loguru import logger

from config import OLLAMA_MODEL, MAX_ARTICLES_PER_DISTRICT
from database import AccidentDB
from agents import OrchestratorAgent, load_states

# ── MCP Server Setup ────────────────────────────────────────────

app = Server("ai-news-accident-crawler")
db = AccidentDB()


# ━━━━━━━  TOOLS  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.list_tools()
async def list_tools():
    return [
        Tool(
            name="crawl_state",
            description="Crawl a specific state for accident news from Dainik Bhaskar. "
                        "Extracts structured data using Ollama LLM and saves to database + Excel.",
            inputSchema={
                "type": "object",
                "properties": {
                    "state": {
                        "type": "string",
                        "description": "State slug (e.g., 'rajasthan', 'mp', 'uttar-pradesh')"
                    },
                    "max_articles": {
                        "type": "integer",
                        "description": "Max articles per district (default: 5)",
                        "default": 5,
                    },
                },
                "required": ["state"],
            },
        ),
        Tool(
            name="crawl_all",
            description="Run full India-wide accident crawl across all 14 states and 211 districts.",
            inputSchema={
                "type": "object",
                "properties": {
                    "max_articles": {
                        "type": "integer",
                        "description": "Max articles per district",
                        "default": 5,
                    },
                },
            },
        ),
        Tool(
            name="get_stats",
            description="Get overall accident database statistics: totals, fatalities, injuries, "
                        "states covered, crawl history.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_daily_report",
            description="Get today's accident crawl summary grouped by state and district.",
            inputSchema={
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date in YYYY-MM-DD format (default: today)",
                    },
                },
            },
        ),
        Tool(
            name="get_state_data",
            description="Query all accident records for a specific state.",
            inputSchema={
                "type": "object",
                "properties": {
                    "state": {
                        "type": "string",
                        "description": "State name in Hindi or English",
                    },
                },
                "required": ["state"],
            },
        ),
        Tool(
            name="search_accidents",
            description="Search accident records by keyword (searches headline, location, district).",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search keyword",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default: 20)",
                        "default": 20,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_crawl_history",
            description="View history of past crawl runs with stats.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "default": 10,
                    },
                },
            },
        ),
        Tool(
            name="list_states",
            description="List all available states and their district counts.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict):
    await db.init()

    if name == "crawl_state":
        state = arguments["state"]
        max_art = arguments.get("max_articles", MAX_ARTICLES_PER_DISTRICT)
        orch = OrchestratorAgent(max_articles=max_art)
        stats = await orch.run([state])
        return [TextContent(
            type="text",
            text=json.dumps(stats, indent=2, ensure_ascii=False, default=str),
        )]

    elif name == "crawl_all":
        max_art = arguments.get("max_articles", MAX_ARTICLES_PER_DISTRICT)
        orch = OrchestratorAgent(max_articles=max_art)
        stats = await orch.run()
        return [TextContent(
            type="text",
            text=json.dumps(stats, indent=2, ensure_ascii=False, default=str),
        )]

    elif name == "get_stats":
        stats = await db.get_stats()
        return [TextContent(type="text", text=json.dumps(stats, indent=2, default=str))]

    elif name == "get_daily_report":
        date = arguments.get("date")
        summary = await db.get_daily_summary(date)
        return [TextContent(
            type="text",
            text=json.dumps(summary, indent=2, ensure_ascii=False, default=str),
        )]

    elif name == "get_state_data":
        state = arguments["state"]
        records = await db.get_state_records(state)
        return [TextContent(
            type="text",
            text=json.dumps(records[:50], indent=2, ensure_ascii=False, default=str),
        )]

    elif name == "search_accidents":
        query = arguments["query"]
        limit = arguments.get("limit", 20)
        results = await db._query(
            """SELECT * FROM accidents
               WHERE headline LIKE ? OR location LIKE ? OR district LIKE ? OR city LIKE ?
               ORDER BY crawl_date DESC LIMIT ?""",
            (f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%", limit),
        )
        return [TextContent(
            type="text",
            text=json.dumps(results, indent=2, ensure_ascii=False, default=str),
        )]

    elif name == "get_crawl_history":
        limit = arguments.get("limit", 10)
        history = await db.get_crawl_history(limit)
        return [TextContent(
            type="text",
            text=json.dumps(history, indent=2, default=str),
        )]

    elif name == "list_states":
        states = load_states()
        info = []
        for slug, data in sorted(states.items()):
            info.append({
                "slug": slug,
                "name": data["name"],
                "districts": len(data.get("districts", {})),
                "url": data["url"],
            })
        return [TextContent(
            type="text",
            text=json.dumps(info, indent=2, ensure_ascii=False),
        )]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


# ━━━━━━━  RESOURCES  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.list_resources()
async def list_resources():
    return [
        Resource(
            uri="accident://stats",
            name="Accident Database Statistics",
            mimeType="application/json",
            description="Live statistics from the accident database",
        ),
        Resource(
            uri="accident://today",
            name="Today's Accident Report",
            mimeType="application/json",
            description="Today's accident crawl summary",
        ),
    ]


@app.read_resource()
async def read_resource(uri: str):
    await db.init()

    if str(uri) == "accident://stats":
        stats = await db.get_stats()
        return json.dumps(stats, indent=2, default=str)

    elif str(uri) == "accident://today":
        summary = await db.get_daily_summary()
        return json.dumps(summary, indent=2, ensure_ascii=False, default=str)

    return json.dumps({"error": f"Unknown resource: {uri}"})


# ━━━━━━━  MAIN  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def main():
    logger.info("🔌 Starting MCP Server: ai-news-accident-crawler")
    logger.info(f"   Model: {OLLAMA_MODEL}")
    logger.info(f"   Tools: crawl_state, crawl_all, get_stats, get_daily_report, ...")

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
