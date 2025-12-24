"""NotebookLM Consumer MCP Server."""

from typing import Any

from fastmcp import FastMCP

from .api_client import ConsumerNotebookLMClient, extract_cookies_from_chrome_export

# Initialize MCP server
mcp = FastMCP(
    name="notebooklm-consumer",
    instructions="""NotebookLM Consumer MCP Server.

**IMPORTANT: This is for Consumer NotebookLM (notebooklm.google.com), NOT Enterprise.**

Consumer NotebookLM is the free/personal tier at notebooklm.google.com.
Enterprise NotebookLM is at vertexaisearch.cloud.google.com (different system, different notebooks).

This MCP uses reverse-engineered internal APIs and requires browser cookie authentication.

## Authentication (SIMPLIFIED!)

You only need to extract COOKIES - the CSRF token and session ID are now auto-extracted!

Using Chrome DevTools MCP:
1. Navigate to notebooklm.google.com
2. Get cookies from any network request: get_network_request(reqid=<any_request>)
3. Call save_auth_tokens(cookies=<cookie_header>)

That's it! Cookies are stable for weeks. The CSRF token and session ID are automatically
extracted from the page when needed.

## Available Tools

- notebook_list: List all notebooks
- notebook_create: Create a new notebook
- notebook_get: Get notebook details with sources
- notebook_rename: Rename a notebook
- notebook_delete: Delete a notebook (REQUIRES user confirmation)
- notebook_add_url: Add URL/YouTube as source
- notebook_add_text: Add pasted text as source
- notebook_add_drive: Add Google Drive document as source
- notebook_query: Ask questions about notebook sources
- source_list_drive: List all sources with types and check Drive freshness
- source_sync_drive: Sync stale Drive sources (REQUIRES user confirmation)
- save_auth_tokens: Save cookies for authentication

## Syncing Drive Sources

To sync outdated Google Drive sources:
1. Call source_list_drive(notebook_id) to see all sources and their freshness
2. Show the user which Drive sources are stale (needs_sync=True)
3. Ask the user to confirm which sources to sync
4. Call source_sync_drive(source_ids, confirm=True) with the confirmed source IDs

## IMPORTANT: Destructive/Modifying Operations

For notebook_delete and source_sync_drive, you MUST:
1. Warn the user about the operation
2. Ask the user explicitly to confirm before proceeding
3. Only set confirm=True after the user approves

## Known Limitations

- Cookies expire after several weeks - re-extract when API calls fail with auth errors
- API is undocumented and may change without notice
- Rate limits apply (~50 queries/day on free tier)
""",
)

# Global state
_client: ConsumerNotebookLMClient | None = None


def get_client() -> ConsumerNotebookLMClient:
    """Get or create the API client.

    Tries environment variables first, falls back to cached tokens from auth CLI.
    """
    global _client
    if _client is None:
        import os

        from .auth import load_cached_tokens

        cookie_header = os.environ.get("NOTEBOOKLM_COOKIES", "")
        csrf_token = os.environ.get("NOTEBOOKLM_CSRF_TOKEN", "")
        session_id = os.environ.get("NOTEBOOKLM_SESSION_ID", "")

        if cookie_header:
            # Use environment variables
            cookies = extract_cookies_from_chrome_export(cookie_header)
        else:
            # Try cached tokens from auth CLI
            cached = load_cached_tokens()
            if cached:
                cookies = cached.cookies
                csrf_token = csrf_token or cached.csrf_token
                session_id = session_id or cached.session_id
            else:
                raise ValueError(
                    "No authentication found. Either:\n"
                    "1. Run 'notebooklm-consumer-auth' to authenticate via Chrome, or\n"
                    "2. Set NOTEBOOKLM_COOKIES environment variable manually"
                )

        _client = ConsumerNotebookLMClient(
            cookies=cookies,
            csrf_token=csrf_token,
            session_id=session_id,
        )
    return _client


@mcp.tool()
def notebook_list(max_results: int = 100) -> dict[str, Any]:
    """List all notebooks.

    Args:
        max_results: Maximum number of notebooks to return (default: 100)

    Returns:
        Dictionary with status and list of notebooks
    """
    try:
        client = get_client()
        notebooks = client.list_notebooks()

        # Count owned vs shared notebooks
        owned_count = sum(1 for nb in notebooks if nb.is_owned)
        shared_count = len(notebooks) - owned_count
        
        # Count notebooks shared by me (owned + is_shared=True)
        shared_by_me_count = sum(1 for nb in notebooks if nb.is_owned and nb.is_shared)

        return {
            "status": "success",
            "count": len(notebooks),
            "owned_count": owned_count,
            "shared_count": shared_count,
            "shared_by_me_count": shared_by_me_count,
            "notebooks": [
                {
                    "id": nb.id,
                    "title": nb.title,
                    "source_count": nb.source_count,
                    "url": nb.url,
                    "ownership": nb.ownership,
                    "is_shared": nb.is_shared,
                }
                for nb in notebooks[:max_results]
            ],
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def notebook_create(title: str = "") -> dict[str, Any]:
    """Create a new notebook.

    Args:
        title: Optional title for the notebook

    Returns:
        Dictionary with status and notebook details
    """
    try:
        client = get_client()
        notebook = client.create_notebook(title=title)

        if notebook:
            return {
                "status": "success",
                "notebook": {
                    "id": notebook.id,
                    "title": notebook.title,
                    "url": notebook.url,
                },
            }
        return {"status": "error", "error": "Failed to create notebook"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def notebook_get(notebook_id: str) -> dict[str, Any]:
    """Get details of a notebook.

    Args:
        notebook_id: The notebook UUID

    Returns:
        Dictionary with status and notebook details
    """
    try:
        client = get_client()
        result = client.get_notebook(notebook_id)

        return {
            "status": "success",
            "notebook": result,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def notebook_add_url(notebook_id: str, url: str) -> dict[str, Any]:
    """Add a URL (website or YouTube) as a source to a notebook.

    Args:
        notebook_id: The notebook UUID
        url: The URL to add

    Returns:
        Dictionary with status and source details
    """
    try:
        client = get_client()
        result = client.add_url_source(notebook_id, url=url)

        if result:
            return {
                "status": "success",
                "source": result,
            }
        return {"status": "error", "error": "Failed to add URL source"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def notebook_add_text(
    notebook_id: str,
    text: str,
    title: str = "Pasted Text",
) -> dict[str, Any]:
    """Add pasted text as a source to a notebook.

    Args:
        notebook_id: The notebook UUID
        text: The text content to add
        title: Optional title for the source

    Returns:
        Dictionary with status and source details
    """
    try:
        client = get_client()
        result = client.add_text_source(notebook_id, text=text, title=title)

        if result:
            return {
                "status": "success",
                "source": result,
            }
        return {"status": "error", "error": "Failed to add text source"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def notebook_add_drive(
    notebook_id: str,
    document_id: str,
    title: str,
    doc_type: str = "doc",
) -> dict[str, Any]:
    """Add a Google Drive document as a source to a notebook.

    Args:
        notebook_id: The notebook UUID
        document_id: The Google Drive document ID (from the URL)
        title: The document title to display
        doc_type: Type of document - "doc", "slides", "sheets", or "pdf"

    Returns:
        Dictionary with status and source details
    """
    try:
        mime_types = {
            "doc": "application/vnd.google-apps.document",
            "docs": "application/vnd.google-apps.document",
            "slides": "application/vnd.google-apps.presentation",
            "sheets": "application/vnd.google-apps.spreadsheet",
            "pdf": "application/pdf",
        }

        mime_type = mime_types.get(doc_type.lower())
        if not mime_type:
            return {
                "status": "error",
                "error": f"Unknown doc_type '{doc_type}'. Use 'doc', 'slides', 'sheets', or 'pdf'.",
            }

        client = get_client()
        result = client.add_drive_source(
            notebook_id,
            document_id=document_id,
            title=title,
            mime_type=mime_type,
        )

        if result:
            return {
                "status": "success",
                "source": result,
            }
        return {"status": "error", "error": "Failed to add Drive source"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def notebook_query(
    notebook_id: str,
    query: str,
    source_ids: list[str] | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Ask a question about the notebook sources.

    Args:
        notebook_id: The notebook UUID
        query: The question to ask
        source_ids: Optional list of source IDs to query (default: all sources)
        conversation_id: Optional conversation ID for follow-up questions

    Returns:
        Dictionary with answer, citations, and conversation_id for follow-ups
    """
    try:
        client = get_client()
        result = client.query(
            notebook_id,
            query_text=query,
            source_ids=source_ids,
            conversation_id=conversation_id,
        )

        if result:
            return {
                "status": "success",
                "answer": result.get("answer", ""),
                "conversation_id": result.get("conversation_id"),
            }
        return {"status": "error", "error": "Failed to query notebook"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def notebook_delete(
    notebook_id: str,
    confirm: bool = False,
) -> dict[str, Any]:
    """Delete a notebook permanently.

    WARNING: This action is IRREVERSIBLE. The notebook and all its sources,
    notes, and generated content will be permanently deleted.

    IMPORTANT: You MUST ask the user for confirmation before calling this tool.
    The confirm parameter must be explicitly set to True.

    Args:
        notebook_id: The notebook UUID to delete
        confirm: Must be True to proceed. Set to False by default as a safety measure.

    Returns:
        Dictionary with status
    """
    if not confirm:
        return {
            "status": "error",
            "error": "Deletion not confirmed. You must ask the user to confirm "
                     "before deleting. Set confirm=True only after user approval.",
            "warning": "This action is IRREVERSIBLE. The notebook and all its "
                       "sources will be permanently deleted.",
        }

    try:
        client = get_client()
        result = client.delete_notebook(notebook_id)

        if result:
            return {
                "status": "success",
                "message": f"Notebook {notebook_id} has been permanently deleted.",
            }
        return {"status": "error", "error": "Failed to delete notebook"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def notebook_rename(
    notebook_id: str,
    new_title: str,
) -> dict[str, Any]:
    """Rename a notebook.

    Args:
        notebook_id: The notebook UUID
        new_title: The new title for the notebook

    Returns:
        Dictionary with status and updated notebook info
    """
    try:
        client = get_client()
        result = client.rename_notebook(notebook_id, new_title)

        if result:
            return {
                "status": "success",
                "notebook": {
                    "id": notebook_id,
                    "title": new_title,
                },
            }
        return {"status": "error", "error": "Failed to rename notebook"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def source_list_drive(notebook_id: str) -> dict[str, Any]:
    """List all sources in a notebook with their types and freshness status.

    This tool identifies which sources can be synced with Google Drive:
    - Type 2 (drive): User-added Google Drive documents
    - Type 1 (gemini_notes): Gemini-generated meeting notes (also stored in Drive)

    Use this before source_sync_drive to see which sources are stale.

    Args:
        notebook_id: The notebook UUID

    Returns:
        Dictionary with sources grouped by syncability and freshness status
    """
    try:
        client = get_client()
        sources = client.get_notebook_sources_with_types(notebook_id)

        # Separate sources by syncability
        syncable_sources = []
        other_sources = []

        for src in sources:
            if src.get("can_sync"):
                # Check freshness for syncable sources (Drive docs and Gemini Notes)
                is_fresh = client.check_source_freshness(src["id"])
                src["is_fresh"] = is_fresh
                src["needs_sync"] = is_fresh is False
                syncable_sources.append(src)
            else:
                other_sources.append(src)

        # Count stale sources
        stale_count = sum(1 for s in syncable_sources if s.get("needs_sync"))

        return {
            "status": "success",
            "notebook_id": notebook_id,
            "summary": {
                "total_sources": len(sources),
                "syncable_sources": len(syncable_sources),
                "stale_sources": stale_count,
                "other_sources": len(other_sources),
            },
            "syncable_sources": syncable_sources,
            "other_sources": [
                {"id": s["id"], "title": s["title"], "type": s["source_type_name"]}
                for s in other_sources
            ],
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def source_sync_drive(
    source_ids: list[str],
    confirm: bool = False,
) -> dict[str, Any]:
    """Sync specified Google Drive sources with their latest content.

    IMPORTANT: Before calling this tool, you MUST:
    1. Call source_list_drive to identify stale sources
    2. Show the user which sources will be synced
    3. Ask the user to confirm they want to sync
    4. Only set confirm=True after user approval

    Args:
        source_ids: List of source UUIDs to sync (get these from source_list_drive)
        confirm: Must be True to proceed. Set to False by default as a safety measure.

    Returns:
        Dictionary with sync results for each source
    """
    if not confirm:
        return {
            "status": "error",
            "error": "Sync not confirmed. You must ask the user to confirm "
                     "before syncing. Set confirm=True only after user approval.",
            "hint": "First call source_list_drive to show stale sources, "
                    "then ask user to confirm before syncing.",
        }

    if not source_ids:
        return {
            "status": "error",
            "error": "No source_ids provided. Use source_list_drive to get source IDs.",
        }

    try:
        client = get_client()
        results = []
        synced_count = 0
        failed_count = 0

        for source_id in source_ids:
            try:
                result = client.sync_drive_source(source_id)
                if result:
                    results.append({
                        "source_id": source_id,
                        "status": "synced",
                        "title": result.get("title"),
                    })
                    synced_count += 1
                else:
                    results.append({
                        "source_id": source_id,
                        "status": "failed",
                        "error": "Sync returned no result",
                    })
                    failed_count += 1
            except Exception as e:
                results.append({
                    "source_id": source_id,
                    "status": "failed",
                    "error": str(e),
                })
                failed_count += 1

        return {
            "status": "success" if failed_count == 0 else "partial",
            "summary": {
                "total": len(source_ids),
                "synced": synced_count,
                "failed": failed_count,
            },
            "results": results,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# Essential cookies for NotebookLM API authentication
# Only these are needed - no need to save all 20+ cookies from the browser
ESSENTIAL_COOKIES = [
    "SID", "HSID", "SSID", "APISID", "SAPISID",  # Core auth cookies
    "__Secure-1PSID", "__Secure-3PSID",  # Secure session variants
    "__Secure-1PAPISID", "__Secure-3PAPISID",  # Secure API variants
    "OSID", "__Secure-OSID",  # Origin-bound session
    "__Secure-1PSIDTS", "__Secure-3PSIDTS",  # Timestamp tokens (rotate frequently)
    "SIDCC", "__Secure-1PSIDCC", "__Secure-3PSIDCC",  # Session cookies (rotate frequently)
]


@mcp.tool()
def save_auth_tokens(
    cookies: str,
    csrf_token: str = "",
    session_id: str = "",
) -> dict[str, Any]:
    """Save authentication cookies for NotebookLM.

    SIMPLIFIED: You only need to provide cookies! The CSRF token and session ID
    are now automatically extracted when needed.

    To extract cookies using Chrome DevTools MCP:
    1. Navigate to notebooklm.google.com
    2. Get cookies from any network request (get_network_request)
    3. Call this tool with the cookie header

    Args:
        cookies: Full cookie header string from a NotebookLM network request
        csrf_token: (DEPRECATED - auto-extracted) CSRF token from page source
        session_id: (DEPRECATED - auto-extracted) Session ID from page source

    Returns:
        Dictionary with status and cache location
    """
    global _client

    try:
        import time
        from .auth import AuthTokens, save_tokens_to_cache

        # Parse cookie string to dict
        all_cookies = {}
        for part in cookies.split("; "):
            if "=" in part:
                key, value = part.split("=", 1)
                all_cookies[key] = value

        # Validate required cookies
        required = ["SID", "HSID", "SSID", "APISID", "SAPISID"]
        missing = [c for c in required if c not in all_cookies]
        if missing:
            return {
                "status": "error",
                "error": f"Missing required cookies: {missing}",
            }

        # Filter to only essential cookies (reduces noise significantly)
        cookie_dict = {k: v for k, v in all_cookies.items() if k in ESSENTIAL_COOKIES}

        # Create and save tokens
        # Note: csrf_token and session_id are now optional - they'll be auto-extracted
        tokens = AuthTokens(
            cookies=cookie_dict,
            csrf_token=csrf_token,  # May be empty - will be auto-extracted
            session_id=session_id,  # May be empty - will be auto-extracted
            extracted_at=time.time(),
        )
        save_tokens_to_cache(tokens)

        # Reset client so next call uses fresh tokens
        _client = None

        from .auth import get_cache_path
        return {
            "status": "success",
            "message": f"Saved {len(cookie_dict)} essential cookies (filtered from {len(all_cookies)}). "
                       f"CSRF token and session ID will be auto-extracted when needed.",
            "cache_path": str(get_cache_path()),
            "note": "You no longer need to extract CSRF token or session ID manually!",
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def main():
    """Run the MCP server."""
    import os

    from .auth import get_cache_path, load_cached_tokens

    # Check authentication sources
    has_env_auth = bool(os.environ.get("NOTEBOOKLM_COOKIES"))
    cached_tokens = load_cached_tokens()

    if not has_env_auth and not cached_tokens:
        print("WARNING: No authentication found.")
        print()
        print("Run 'notebooklm-consumer-auth' to authenticate via Chrome.")
        print("(It will launch Chrome automatically if needed)")
        print()
    elif cached_tokens and not has_env_auth:
        print(f"Using cached auth tokens from {get_cache_path()}")

    print("NotebookLM Consumer MCP Server starting...")
    mcp.run()
    return 0


if __name__ == "__main__":
    exit(main())
