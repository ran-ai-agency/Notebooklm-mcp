#!/usr/bin/env python3
"""
POC for Consumer NotebookLM (notebooklm.google.com) internal API.

This is a research POC - NOT for production use. The API is undocumented
and may change at any time.

IMPORTANT: This is a SEPARATE system from NotebookLM Enterprise (Vertex AI).
- Consumer: notebooklm.google.com (this POC)
- Enterprise: vertexaisearch.cloud.google.com/notebooklm/ (the MCP server)

## API Discovery Summary

Endpoint: https://notebooklm.google.com/_/LabsTailwindUi/data/batchexecute
Method: POST (application/x-www-form-urlencoded)

Request format:
  - f.req: URL-encoded JSON [[["rpc_id", "params_json", null, "generic"]]]
  - at: CSRF token (from WIZ_global_data.SNlM0e in page)
  - URL params: rpcids, source-path, bl (build), f.sid, hl, rt

Known RPC IDs:
  - wXbhsf: List notebooks with sources (returns ALL notebooks, filtering is client-side)
  - rLM1Ne: Get notebook details
  - CCqFvf: Create new notebook
  - izAoDd: Add source (URL, text, or Drive) - unified RPC for all source types
  - hPTbtc: Get conversation IDs
  - hT54vc: User preferences (filter settings)
  - ZwVcOc: Settings/preferences
  - ozz5Z: Subscription info

Query Endpoint (streaming, NOT batchexecute):
  - POST /_/LabsTailwindUi/data/google.internal.labs.tailwind.orchestration.v1.LabsTailwindOrchestrationService/GenerateFreeFormStreamed
  - Params: [[[source_ids]], query_text, null, [2, null, [1]], conversation_id]
  - Response: Streaming JSON with thinking steps + final answer with citations

Source Types (via izAoDd):
  - URL/YouTube: [null, null, [urls], null, null, null, null, null, null, null, 1]
  - Pasted Text: [null, [title, content], null, 2, null, null, null, null, null, null, 1]
  - Google Drive: [[doc_id, mime_type, 1, title], null, null, null, null, null, null, null, null, null, 1]

Filtering:
  - "My notebooks" vs "Shared with me" filtering is done CLIENT-SIDE
  - The list response includes ownership info that can be used to filter
  - No API parameter to filter by ownership

Response format:
  - Starts with ")]}'" (anti-XSSI prefix)
  - Followed by byte count, then JSON array

Authentication:
  - Cookie-based: SID, SSID, HSID, APISID, SAPISID, OSID, etc.
  - CSRF token required (changes per page load)
  - Session ID (f.sid) required

## Usage

1. Log in to notebooklm.google.com in Chrome
2. Open DevTools > Network tab
3. Find any POST to /_/LabsTailwindUi/data/batchexecute
4. Copy the Cookie header value
5. Copy CSRF token (at= in request body) and session ID (f.sid= in URL)
6. Run: python consumer_notebooklm.py 'COOKIE_HEADER'
"""

import json
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx


# Ownership constants (from metadata position 0)
OWNERSHIP_MINE = 1          # Created by me
OWNERSHIP_SHARED = 2        # Shared with me by someone else


@dataclass
class ConsumerNotebook:
    """Represents a consumer NotebookLM notebook."""

    id: str
    title: str
    source_count: int
    sources: list[dict]
    is_owned: bool = True     # True if owned by user, False if shared with user
    is_shared: bool = False   # True if shared with others (for owned notebooks)

    @property
    def url(self) -> str:
        return f"https://notebooklm.google.com/notebook/{self.id}"

    @property
    def ownership(self) -> str:
        """Return human-readable ownership status."""
        if self.is_owned:
            return "owned"
        return "shared_with_me"


class ConsumerNotebookLMClient:
    """Client for consumer NotebookLM internal API."""

    BASE_URL = "https://notebooklm.google.com"
    BATCHEXECUTE_URL = f"{BASE_URL}/_/LabsTailwindUi/data/batchexecute"

    # Known RPC IDs
    RPC_LIST_NOTEBOOKS = "wXbhsf"
    RPC_GET_NOTEBOOK = "rLM1Ne"
    RPC_CREATE_NOTEBOOK = "CCqFvf"
    RPC_RENAME_NOTEBOOK = "s0tc2d"
    RPC_DELETE_NOTEBOOK = "WWINqb"
    RPC_ADD_SOURCE = "izAoDd"  # Used for URL, text, and Drive sources
    RPC_GET_SOURCE = "hizoJc"  # Get source details
    RPC_CHECK_FRESHNESS = "yR9Yof"  # Check if Drive source is stale
    RPC_SYNC_DRIVE = "FLmJqe"  # Sync Drive source with latest content
    RPC_GET_CONVERSATIONS = "hPTbtc"
    RPC_PREFERENCES = "hT54vc"
    RPC_SUBSCRIPTION = "ozz5Z"
    RPC_SETTINGS = "ZwVcOc"

    # Source type constants (from metadata position 4)
    # These represent the Google Workspace document type, NOT the source origin
    SOURCE_TYPE_GOOGLE_DOCS = 1           # Google Docs (Documents)
    SOURCE_TYPE_GOOGLE_OTHER = 2          # Google Slides & Sheets (non-Doc Drive files)
    SOURCE_TYPE_PASTED_TEXT = 4           # Pasted text (not from Drive)

    # Query endpoint (different from batchexecute - streaming gRPC-style)
    QUERY_ENDPOINT = "/_/LabsTailwindUi/data/google.internal.labs.tailwind.orchestration.v1.LabsTailwindOrchestrationService/GenerateFreeFormStreamed"

    # Headers required for page fetch (must look like a browser navigation)
    _PAGE_FETCH_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "sec-ch-ua": '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
    }

    def __init__(self, cookies: dict[str, str], csrf_token: str = "", session_id: str = ""):
        """
        Initialize the client.

        Args:
            cookies: Dict of Google auth cookies (SID, SSID, HSID, APISID, SAPISID, etc.)
            csrf_token: CSRF token (optional - will be auto-extracted from page if not provided)
            session_id: Session ID (optional - will be auto-extracted from page if not provided)
        """
        self.cookies = cookies
        self.csrf_token = csrf_token
        self._client: httpx.Client | None = None
        self._session_id = session_id

        # Auto-refresh tokens if not provided
        if not self.csrf_token:
            self._refresh_auth_tokens()

    def _refresh_auth_tokens(self) -> None:
        """
        Refresh CSRF token and session ID by fetching the NotebookLM homepage.

        This method fetches the NotebookLM page using the stored cookies and
        extracts the CSRF token (SNlM0e) and session ID (FdrFJe) from the HTML.

        Raises:
            ValueError: If cookies are expired (redirected to login) or tokens not found
        """
        # Build cookie header
        cookie_header = "; ".join(f"{k}={v}" for k, v in self.cookies.items())

        # Must use browser-like headers for page fetch
        headers = {**self._PAGE_FETCH_HEADERS, "Cookie": cookie_header}

        # Use a temporary client for the page fetch
        with httpx.Client(headers=headers, follow_redirects=True, timeout=15.0) as client:
            response = client.get(f"{self.BASE_URL}/")

            # Check if redirected to login (cookies expired)
            if "accounts.google.com" in str(response.url):
                raise ValueError(
                    "Cookies have expired. Please re-authenticate by extracting fresh cookies "
                    "from Chrome DevTools and calling save_auth_tokens."
                )

            if response.status_code != 200:
                raise ValueError(f"Failed to fetch NotebookLM page: HTTP {response.status_code}")

            html = response.text

            # Extract CSRF token (SNlM0e)
            csrf_match = re.search(r'"SNlM0e":"([^"]+)"', html)
            if not csrf_match:
                # Save HTML for debugging
                from pathlib import Path
                debug_dir = Path.home() / ".notebooklm-consumer"
                debug_dir.mkdir(exist_ok=True)
                debug_path = debug_dir / "debug_page.html"
                debug_path.write_text(html)
                raise ValueError(
                    f"Could not extract CSRF token from page. "
                    f"Page saved to {debug_path} for debugging. "
                    f"The page structure may have changed."
                )

            self.csrf_token = csrf_match.group(1)

            # Extract session ID (FdrFJe) - optional but helps
            sid_match = re.search(r'"FdrFJe":"([^"]+)"', html)
            if sid_match:
                self._session_id = sid_match.group(1)

    def _get_client(self) -> httpx.Client:
        """Get or create HTTP client."""
        if self._client is None:
            # Build cookie string
            cookie_str = "; ".join(f"{k}={v}" for k, v in self.cookies.items())

            self._client = httpx.Client(
                headers={
                    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                    "Origin": self.BASE_URL,
                    "Referer": f"{self.BASE_URL}/",
                    "Cookie": cookie_str,
                    "X-Same-Domain": "1",
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                },
                timeout=30.0,
            )
        return self._client

    def _build_request_body(self, rpc_id: str, params: Any) -> str:
        """Build the batchexecute request body."""
        # The params need to be JSON-encoded, then wrapped in the RPC structure
        params_json = json.dumps(params)

        # Build the f.req structure
        f_req = [[[rpc_id, params_json, None, "generic"]]]
        f_req_json = json.dumps(f_req)

        # URL encode
        body_parts = [f"f.req={urllib.parse.quote(f_req_json)}"]

        if self.csrf_token:
            body_parts.append(f"at={urllib.parse.quote(self.csrf_token)}")

        return "&".join(body_parts)

    def _build_url(self, rpc_id: str, source_path: str = "/") -> str:
        """Build the batchexecute URL with query params."""
        params = {
            "rpcids": rpc_id,
            "source-path": source_path,
            "bl": "boq_labs-tailwind-frontend_20251217.10_p0",  # Version string, may change
            "hl": "en",
            "rt": "c",
        }

        if self._session_id:
            params["f.sid"] = self._session_id

        query = urllib.parse.urlencode(params)
        return f"{self.BATCHEXECUTE_URL}?{query}"

    def _parse_response(self, response_text: str) -> Any:
        """Parse the batchexecute response."""
        # Response format:
        # )]}'
        # <byte_count>
        # <json_array>

        # Remove the anti-XSSI prefix
        if response_text.startswith(")]}'"):
            response_text = response_text[4:]

        lines = response_text.strip().split("\n")

        # Parse each chunk
        results = []
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1
                continue

            # Try to parse as byte count
            try:
                byte_count = int(line)
                # Next line(s) should be the JSON payload
                i += 1
                if i < len(lines):
                    json_str = lines[i]
                    try:
                        data = json.loads(json_str)
                        results.append(data)
                    except json.JSONDecodeError:
                        pass
                i += 1
            except ValueError:
                # Not a byte count, try to parse as JSON
                try:
                    data = json.loads(line)
                    results.append(data)
                except json.JSONDecodeError:
                    pass
                i += 1

        return results

    def _extract_rpc_result(self, parsed_response: list, rpc_id: str) -> Any:
        """Extract the result for a specific RPC ID from the parsed response."""
        for chunk in parsed_response:
            if isinstance(chunk, list):
                for item in chunk:
                    if isinstance(item, list) and len(item) >= 3:
                        if item[0] == "wrb.fr" and item[1] == rpc_id:
                            # The result is in item[2] as a JSON string
                            result_str = item[2]
                            if isinstance(result_str, str):
                                try:
                                    return json.loads(result_str)
                                except json.JSONDecodeError:
                                    return result_str
                            return result_str
        return None

    def list_notebooks(self, debug: bool = False) -> list[ConsumerNotebook]:
        """List all notebooks."""
        client = self._get_client()

        # Build request
        # [null, 1, null, [2]] - params for list notebooks
        params = [None, 1, None, [2]]
        body = self._build_request_body(self.RPC_LIST_NOTEBOOKS, params)
        url = self._build_url(self.RPC_LIST_NOTEBOOKS)

        if debug:
            print(f"[DEBUG] URL: {url}")
            print(f"[DEBUG] Body: {body[:200]}...")

        response = client.post(url, content=body)
        response.raise_for_status()

        if debug:
            print(f"[DEBUG] Response status: {response.status_code}")
            print(f"[DEBUG] Response length: {len(response.text)} chars")

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_LIST_NOTEBOOKS)

        if debug:
            print(f"[DEBUG] Parsed chunks: {len(parsed)}")
            print(f"[DEBUG] Result type: {type(result)}")
            if result:
                print(f"[DEBUG] Result length: {len(result) if isinstance(result, list) else 'N/A'}")
                if isinstance(result, list) and len(result) > 0:
                    print(f"[DEBUG] First item type: {type(result[0])}")
                    print(f"[DEBUG] First item: {str(result[0])[:500]}...")

        notebooks = []
        if result and isinstance(result, list):
            # Result structure: [[notebook1, notebook2, ...]]
            # Each notebook structure:
            #   [0] = "Title"
            #   [1] = [sources]
            #   [2] = "notebook-uuid"
            #   [3] = "emoji" or null
            #   [4] = null
            #   [5] = [metadata] where metadata[0] = ownership (1=mine, 2=shared_with_me)
            notebook_list = result[0] if result and isinstance(result[0], list) else result

            for nb_data in notebook_list:
                if isinstance(nb_data, list) and len(nb_data) >= 3:
                    title = nb_data[0] if isinstance(nb_data[0], str) else "Untitled"
                    sources_data = nb_data[1] if len(nb_data) > 1 else []
                    notebook_id = nb_data[2] if len(nb_data) > 2 else None

                    # Extract ownership from metadata at position 5
                    is_owned = True  # Default to owned
                    is_shared = False # Default to not shared
                    if len(nb_data) > 5 and isinstance(nb_data[5], list) and len(nb_data[5]) > 0:
                        metadata = nb_data[5]
                        ownership_value = metadata[0]
                        # 1 = mine (owned), 2 = shared with me
                        is_owned = ownership_value == OWNERSHIP_MINE
                        
                        # Check if shared (for owned notebooks)
                        # Based on observation: [1, true, true, ...] -> Shared
                        #                       [1, false, true, ...] -> Private
                        if len(metadata) > 1:
                            is_shared = bool(metadata[1])

                    sources = []
                    if isinstance(sources_data, list):
                        for src in sources_data:
                            if isinstance(src, list) and len(src) >= 2:
                                # Source structure: [[source_id], title, metadata, ...]
                                src_ids = src[0] if src[0] else []
                                src_title = src[1] if len(src) > 1 else "Untitled"

                                # Extract the source ID (might be in a list)
                                src_id = src_ids[0] if isinstance(src_ids, list) and src_ids else src_ids

                                sources.append({
                                    "id": src_id,
                                    "title": src_title,
                                })

                    if notebook_id:
                        notebooks.append(ConsumerNotebook(
                            id=notebook_id,
                            title=title,
                            source_count=len(sources),
                            sources=sources,
                            is_owned=is_owned,
                            is_shared=is_shared,
                        ))

        return notebooks

    def get_notebook(self, notebook_id: str) -> dict | None:
        """Get details of a specific notebook."""
        client = self._get_client()

        # [notebook_id, null, [2], null, 0]
        params = [notebook_id, None, [2], None, 0]
        body = self._build_request_body(self.RPC_GET_NOTEBOOK, params)
        url = self._build_url(self.RPC_GET_NOTEBOOK, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_GET_NOTEBOOK)

        return result

    def create_notebook(self, title: str = "") -> ConsumerNotebook | None:
        """Create a new notebook.

        Args:
            title: Optional title for the notebook (empty string for "Untitled notebook")

        Returns:
            ConsumerNotebook with the new notebook's ID, or None on failure
        """
        client = self._get_client()

        # Create notebook params: [title, null, null, [2], [1, null, null, null, null, null, null, null, null, null, [1]]]
        params = [title, None, None, [2], [1, None, None, None, None, None, None, None, None, None, [1]]]
        body = self._build_request_body(self.RPC_CREATE_NOTEBOOK, params)
        url = self._build_url(self.RPC_CREATE_NOTEBOOK)

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_CREATE_NOTEBOOK)

        if result and isinstance(result, list) and len(result) >= 3:
            # Response: ["", null, "notebook-uuid", ...]
            notebook_id = result[2]
            if notebook_id:
                return ConsumerNotebook(
                    id=notebook_id,
                    title=title or "Untitled notebook",
                    source_count=0,
                    sources=[],
                )
        return None

    def rename_notebook(self, notebook_id: str, new_title: str) -> bool:
        """Rename a notebook.

        Args:
            notebook_id: The notebook UUID
            new_title: The new title for the notebook

        Returns:
            True on success, False on failure
        """
        client = self._get_client()

        # Rename notebook params: [notebook_id, [[null, null, null, [null, "New Title"]]]]
        params = [notebook_id, [[None, None, None, [None, new_title]]]]
        body = self._build_request_body(self.RPC_RENAME_NOTEBOOK, params)
        url = self._build_url(self.RPC_RENAME_NOTEBOOK, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_RENAME_NOTEBOOK)

        # Success returns the notebook info with new title
        return result is not None

    def delete_notebook(self, notebook_id: str) -> bool:
        """Delete a notebook permanently.

        WARNING: This action is IRREVERSIBLE. The notebook and all its sources,
        notes, and generated content will be permanently deleted.

        Args:
            notebook_id: The notebook UUID to delete

        Returns:
            True on success, False on failure
        """
        client = self._get_client()

        # Delete notebook params: [[notebook_id], [2]]
        params = [[notebook_id], [2]]
        body = self._build_request_body(self.RPC_DELETE_NOTEBOOK, params)
        url = self._build_url(self.RPC_DELETE_NOTEBOOK)

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_DELETE_NOTEBOOK)

        # Success returns empty list []
        return result is not None

    def check_source_freshness(self, source_id: str) -> bool | None:
        """Check if a Drive source is fresh (up-to-date with Google Drive).

        Args:
            source_id: The source UUID

        Returns:
            True if fresh, False if stale (needs sync), None on error
        """
        client = self._get_client()

        # Check freshness params: [null, ["source_id"], [2]]
        params = [None, [source_id], [2]]
        body = self._build_request_body(self.RPC_CHECK_FRESHNESS, params)
        url = self._build_url(self.RPC_CHECK_FRESHNESS)

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_CHECK_FRESHNESS)

        # Response: [[null, true/false, ["source_id"]]]
        # true = fresh, false = stale
        if result and isinstance(result, list) and len(result) > 0:
            inner = result[0] if result else []
            if isinstance(inner, list) and len(inner) >= 2:
                return inner[1]  # true = fresh, false = stale
        return None

    def sync_drive_source(self, source_id: str) -> dict | None:
        """Sync a Drive source with the latest content from Google Drive.

        Args:
            source_id: The source UUID

        Returns:
            Dict with updated source info (id, title, synced_at) or None on failure
        """
        client = self._get_client()

        # Sync params: [null, ["source_id"], [2]]
        params = [None, [source_id], [2]]
        body = self._build_request_body(self.RPC_SYNC_DRIVE, params)
        url = self._build_url(self.RPC_SYNC_DRIVE)

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_SYNC_DRIVE)

        # Response: [[[source_id], "title", [metadata...], [null, 2]]]
        if result and isinstance(result, list) and len(result) > 0:
            source_data = result[0] if result else []
            if isinstance(source_data, list) and len(source_data) >= 3:
                source_id_result = source_data[0][0] if source_data[0] else None
                title = source_data[1] if len(source_data) > 1 else "Unknown"
                metadata = source_data[2] if len(source_data) > 2 else []

                # Extract sync timestamp from metadata[3]
                synced_at = None
                if isinstance(metadata, list) and len(metadata) > 3:
                    sync_info = metadata[3]
                    if isinstance(sync_info, list) and len(sync_info) > 1:
                        ts = sync_info[1]
                        if isinstance(ts, list) and len(ts) > 0:
                            synced_at = ts[0]

                return {
                    "id": source_id_result,
                    "title": title,
                    "synced_at": synced_at,
                }
        return None

    def get_notebook_sources_with_types(self, notebook_id: str) -> list[dict]:
        """Get all sources from a notebook with their type information.

        Args:
            notebook_id: The notebook UUID

        Returns:
            List of source dicts with id, title, source_type, and drive_doc_id
        """
        result = self.get_notebook(notebook_id)

        sources = []
        # Result structure: [[title, [sources], notebook_id, ...]]
        # The notebook data is wrapped in an outer array
        if result and isinstance(result, list) and len(result) >= 1:
            notebook_data = result[0] if isinstance(result[0], list) else result
            # Sources are in notebook_data[1]
            sources_data = notebook_data[1] if len(notebook_data) > 1 else []

            if isinstance(sources_data, list):
                for src in sources_data:
                    if isinstance(src, list) and len(src) >= 3:
                        # Source structure: [[id], title, [metadata...], [null, 2]]
                        source_id = src[0][0] if src[0] and isinstance(src[0], list) else None
                        title = src[1] if len(src) > 1 else "Untitled"
                        metadata = src[2] if len(src) > 2 else []

                        # Extract source type from metadata[4]
                        source_type = None
                        drive_doc_id = None
                        if isinstance(metadata, list):
                            if len(metadata) > 4:
                                source_type = metadata[4]
                            # Drive doc info at metadata[0]
                            if len(metadata) > 0 and isinstance(metadata[0], list):
                                drive_doc_id = metadata[0][0] if metadata[0] else None

                        # Google Docs (type 1) and Slides/Sheets (type 2) are stored in Drive
                        # and can be synced if they have a drive_doc_id
                        can_sync = drive_doc_id is not None and source_type in (
                            self.SOURCE_TYPE_GOOGLE_DOCS,
                            self.SOURCE_TYPE_GOOGLE_OTHER,
                        )

                        sources.append({
                            "id": source_id,
                            "title": title,
                            "source_type": source_type,
                            "source_type_name": self._get_source_type_name(source_type),
                            "drive_doc_id": drive_doc_id,
                            "can_sync": can_sync,  # True for Drive docs AND Gemini Notes
                        })

        return sources

    @staticmethod
    def _get_source_type_name(source_type: int | None) -> str:
        """Convert source type number to human-readable name."""
        if source_type == 1:
            return "google_docs"
        elif source_type == 2:
            return "google_slides_sheets"  # Slides and Sheets both use type 2
        elif source_type == 4:
            return "pasted_text"
        return "unknown"

    def add_url_source(self, notebook_id: str, url: str) -> dict | None:
        """Add a URL (website or YouTube) as a source to a notebook.

        Args:
            notebook_id: The notebook UUID
            url: The URL to add (website or YouTube video)

        Returns:
            Dict with source info (id, title) or None on failure
        """
        client = self._get_client()

        # URL source params structure:
        # [[[null, null, [urls], null, null, null, null, null, null, null, 1]], notebook_id, [2], settings]
        source_data = [None, None, [url], None, None, None, None, None, None, None, 1]
        params = [
            [[source_data]],
            notebook_id,
            [2],
            [1, None, None, None, None, None, None, None, None, None, [1]]
        ]
        body = self._build_request_body(self.RPC_ADD_SOURCE, params)
        source_path = f"/notebook/{notebook_id}"
        url_endpoint = self._build_url(self.RPC_ADD_SOURCE, source_path)

        response = client.post(url_endpoint, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_ADD_SOURCE)

        if result and isinstance(result, list) and len(result) > 0:
            # Response: [[[[source_id], title, metadata, ...]]]
            source_list = result[0] if result else []
            if source_list and len(source_list) > 0:
                source_data = source_list[0]
                source_id = source_data[0][0] if source_data[0] else None
                source_title = source_data[1] if len(source_data) > 1 else "Untitled"
                return {"id": source_id, "title": source_title}
        return None

    def add_text_source(self, notebook_id: str, text: str, title: str = "Pasted Text") -> dict | None:
        """Add pasted text as a source to a notebook.

        Args:
            notebook_id: The notebook UUID
            text: The text content to add
            title: Optional title for the source (default: "Pasted Text")

        Returns:
            Dict with source info (id, title) or None on failure
        """
        client = self._get_client()

        # Text source params structure:
        # [[[null, [title, content], null, 2, null, null, null, null, null, null, 1]], notebook_id, [2], settings]
        source_data = [None, [title, text], None, 2, None, None, None, None, None, None, 1]
        params = [
            [[source_data]],
            notebook_id,
            [2],
            [1, None, None, None, None, None, None, None, None, None, [1]]
        ]
        body = self._build_request_body(self.RPC_ADD_SOURCE, params)
        source_path = f"/notebook/{notebook_id}"
        url_endpoint = self._build_url(self.RPC_ADD_SOURCE, source_path)

        response = client.post(url_endpoint, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_ADD_SOURCE)

        if result and isinstance(result, list) and len(result) > 0:
            source_list = result[0] if result else []
            if source_list and len(source_list) > 0:
                source_data = source_list[0]
                source_id = source_data[0][0] if source_data[0] else None
                source_title = source_data[1] if len(source_data) > 1 else title
                return {"id": source_id, "title": source_title}
        return None

    def add_drive_source(
        self,
        notebook_id: str,
        document_id: str,
        title: str,
        mime_type: str = "application/vnd.google-apps.document"
    ) -> dict | None:
        """Add a Google Drive document as a source to a notebook.

        Args:
            notebook_id: The notebook UUID
            document_id: The Google Drive document ID (from the URL)
            title: The document title/name to display
            mime_type: The MIME type (default: Google Doc)
                - application/vnd.google-apps.document (Google Doc)
                - application/vnd.google-apps.presentation (Google Slides)
                - application/vnd.google-apps.spreadsheet (Google Sheets)
                - application/pdf (PDF)

        Returns:
            Dict with source info (id, title) or None on failure
        """
        client = self._get_client()

        # Drive source params structure (verified from network capture):
        # [[doc_id, mime_type, 1, title], null, null, null, null, null, null, null, null, null, 1]
        source_data = [
            [document_id, mime_type, 1, title],  # Drive document info at position 0
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            1
        ]
        params = [
            [[source_data]],
            notebook_id,
            [2],
            [1, None, None, None, None, None, None, None, None, None, [1]]
        ]
        body = self._build_request_body(self.RPC_ADD_SOURCE, params)
        source_path = f"/notebook/{notebook_id}"
        url_endpoint = self._build_url(self.RPC_ADD_SOURCE, source_path)

        response = client.post(url_endpoint, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_ADD_SOURCE)

        if result and isinstance(result, list) and len(result) > 0:
            source_list = result[0] if result else []
            if source_list and len(source_list) > 0:
                source_data = source_list[0]
                source_id = source_data[0][0] if source_data[0] else None
                source_title = source_data[1] if len(source_data) > 1 else document_name
                return {"id": source_id, "title": source_title}
        return None

    def query(
        self,
        notebook_id: str,
        query_text: str,
        source_ids: list[str] | None = None,
        conversation_id: str | None = None,
    ) -> dict | None:
        """Query the notebook with a question.

        Args:
            notebook_id: The notebook UUID
            query_text: The question to ask
            source_ids: Optional list of source IDs to query (default: all sources)
            conversation_id: Optional conversation ID for follow-up questions

        Returns:
            Dict with answer text, citations, and conversation_id for follow-ups
        """
        import uuid

        client = self._get_client()

        # If no source_ids provided, get them from the notebook
        if source_ids is None:
            notebook_data = self.get_notebook(notebook_id)
            if notebook_data and isinstance(notebook_data, list) and len(notebook_data) > 0:
                # Extract source IDs from notebook data
                # Structure varies, try to find source IDs
                source_ids = []
                # This needs refinement based on actual notebook structure
            else:
                source_ids = []

        # Generate conversation ID if not provided
        if conversation_id is None:
            conversation_id = str(uuid.uuid4())

        # Build source IDs structure: [[["id1"]], [["id2"]], ...]
        sources_array = [[[[sid]]] for sid in source_ids] if source_ids else []

        # Query params structure (from network capture)
        params = [
            sources_array,
            query_text,
            None,
            [2, None, [1]],
            conversation_id
        ]
        params_json = json.dumps(params)

        # Build request body (similar to batchexecute but different structure)
        f_req = [None, params_json]
        f_req_json = json.dumps(f_req)

        body_parts = [f"f.req={urllib.parse.quote(f_req_json)}"]
        if self.csrf_token:
            body_parts.append(f"at={urllib.parse.quote(self.csrf_token)}")
        body = "&".join(body_parts)

        # Build URL
        url_params = {
            "bl": "boq_labs-tailwind-frontend_20251217.10_p0",
            "hl": "en",
            "rt": "c",
        }
        if self._session_id:
            url_params["f.sid"] = self._session_id

        query_string = urllib.parse.urlencode(url_params)
        url = f"{self.BASE_URL}{self.QUERY_ENDPOINT}?{query_string}"

        response = client.post(url, content=body)
        response.raise_for_status()

        # Parse streaming response - collect all chunks
        parsed = self._parse_response(response.text)

        # Extract final answer from the last chunk
        answer_text = ""
        for chunk in reversed(parsed):
            if isinstance(chunk, list):
                for item in chunk:
                    if isinstance(item, list) and len(item) >= 3:
                        if item[0] == "wrb.fr" and item[2]:
                            try:
                                result = json.loads(item[2])
                                if result and isinstance(result, list) and len(result) > 0:
                                    inner = result[0]
                                    if isinstance(inner, list) and len(inner) > 0:
                                        answer_text = inner[0]
                                        break
                            except json.JSONDecodeError:
                                pass
                if answer_text:
                    break

        return {
            "answer": answer_text,
            "conversation_id": conversation_id,
            "raw_response": parsed,
        }

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None


def extract_cookies_from_chrome_export(cookie_header: str) -> dict[str, str]:
    """
    Extract cookies from a copy-pasted cookie header value.

    Usage:
    1. Go to notebooklm.google.com in Chrome
    2. Open DevTools > Network tab
    3. Refresh and find any request to notebooklm.google.com
    4. Copy the Cookie header value
    5. Pass it to this function
    """
    cookies = {}
    for part in cookie_header.split(";"):
        part = part.strip()
        if "=" in part:
            key, value = part.split("=", 1)
            cookies[key.strip()] = value.strip()
    return cookies


# Example usage (for testing)
if __name__ == "__main__":
    import sys

    print("Consumer NotebookLM API POC")
    print("=" * 50)
    print()
    print("To use this POC, you need to:")
    print("1. Go to notebooklm.google.com in Chrome")
    print("2. Open DevTools > Network tab")
    print("3. Find a request to notebooklm.google.com")
    print("4. Copy the entire Cookie header value")
    print()
    print("Then run:")
    print("  python consumer_notebooklm.py 'YOUR_COOKIE_HEADER'")
    print()

    if len(sys.argv) > 1:
        cookie_header = sys.argv[1]
        cookies = extract_cookies_from_chrome_export(cookie_header)

        print(f"Extracted {len(cookies)} cookies")
        print()

        # Session tokens - these need to be extracted from the page
        # To get these:
        # 1. Go to notebooklm.google.com in Chrome
        # 2. Open DevTools > Network tab
        # 3. Find any POST request to /_/LabsTailwindUi/data/batchexecute
        # 4. CSRF token: Look for 'at=' parameter in the request body
        # 5. Session ID: Look for 'f.sid=' parameter in the URL
        #
        # These tokens are session-specific and expire after some time.
        # For automated use, you'd need to extract them from the page's JavaScript.

        # Get tokens from environment or use defaults (update these if needed)
        import os
        csrf_token = os.environ.get(
            "NOTEBOOKLM_CSRF_TOKEN",
            "ACi2F2OxJshr6FHHGUtehylr0NVT:1766372302394"  # Update this
        )
        session_id = os.environ.get(
            "NOTEBOOKLM_SESSION_ID",
            "1975517010764758431"  # Update this
        )

        print(f"Using CSRF token: {csrf_token[:20]}...")
        print(f"Using session ID: {session_id}")
        print()

        client = ConsumerNotebookLMClient(cookies, csrf_token=csrf_token, session_id=session_id)

        try:
            # Demo: List notebooks
            print("Listing notebooks...")
            print()

            notebooks = client.list_notebooks(debug=False)

            print(f"Found {len(notebooks)} notebooks:")
            for nb in notebooks[:5]:  # Limit output
                print(f"  - {nb.title}")
                print(f"    ID: {nb.id}")
                print(f"    URL: {nb.url}")
                print(f"    Sources: {nb.source_count}")
                print()

            # Demo: Create a notebook (commented out to avoid creating test notebooks)
            # print("Creating a new notebook...")
            # new_nb = client.create_notebook(title="Test Notebook from API")
            # if new_nb:
            #     print(f"Created notebook: {new_nb.title}")
            #     print(f"  ID: {new_nb.id}")
            #     print(f"  URL: {new_nb.url}")

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Error: {e}")
        finally:
            client.close()
