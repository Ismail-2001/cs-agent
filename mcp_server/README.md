# MCP Server for Customer Support AI Employee

This directory contains a Model Context Protocol (MCP) server that exposes read-only tools for querying the support agent's data. This allows you to plug "your support agent" directly into Claude Desktop, Claude Code, or any other MCP-compatible host.

## What's Exposed

The MCP server provides these read-only tools:

- **lookup_order(order_number)**: Look up a Shopify order and get a human-readable summary
- **search_knowledge_base(query, top_k)**: Search policies, FAQs, and product specs
- **get_ticket(ticket_id)**: Retrieve a support ticket with AI suggestion
- **list_open_tickets(limit)**: List open/in-progress tickets

**Important**: All tools are READ-ONLY. No write/mutating operations (refunds, status changes, etc.) are exposed via MCP to maintain the human-approval workflow.

## Claude Desktop Configuration

To connect this MCP server to Claude Desktop, add this to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "support-agent": {
      "command": "python",
      "args": [
        "-m",
        "mcp_server.server"
      ],
      "env": {
        "TENANT_NAME": "your-client-name",
        "OPENROUTER_API_KEY": "your-openrouter-api-key",
        "SHOPIFY_SHOP_DOMAIN": "your-store.myshopify.com",
        "SHOPIFY_ACCESS_TOKEN": "shpat_xxxxxxxxxxxx",
        "DB_PATH": "cs_agent.db"
      }
    }
  }
}
```

### Required Environment Variables

- `TENANT_NAME`: Your client/tenant identifier (required)
- `OPENROUTER_API_KEY`: For LLM embeddings in knowledge base search (required for KB search)
- `SHOPIFY_SHOP_DOMAIN`: Your Shopify store domain (required for order lookup)
- `SHOPIFY_ACCESS_TOKEN`: Shopify Admin API access token (required for order lookup)
- `DB_PATH`: Path to SQLite database (defaults to `cs_agent.db`)

## Running Locally

```bash
# From the repository root
python -m mcp_server.server
```

The server uses stdio transport, which is the standard for Claude Desktop/Claude Code.

## Testing with MCP Inspector

To test the server without Claude Desktop:

```bash
# Terminal 1: Start the server
python -m mcp_server.server

# Terminal 2: Run MCP Inspector
npx -y @modelcontextprotocol/inspector
```

Then connect to the stdio transport in the inspector UI.

## Security Notes

- This server exposes READ-ONLY operations only
- It reuses the same configuration and authentication as the main REST API
- No additional auth is required beyond the environment variables
- The server runs with the same database and Shopify credentials as the main application
