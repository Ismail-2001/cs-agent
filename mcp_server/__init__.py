"""MCP server for the Customer Support AI Employee.

This server exposes read-only tools for querying Shopify orders, knowledge base,
and support tickets via the Model Context Protocol (MCP). It's designed to be
used with Claude Desktop, Claude Code, or any other MCP-compatible host.

All tools are READ-ONLY - no write/mutating operations are exposed to ensure
safety and maintain the human-approval workflow for actions like refunds.
"""
