"""
Preferred Links Manager
Handles storage and retrieval of user-defined preferred links using JSON file storage.
"""

import json
import os
import logging
from pathlib import Path
from typing import List, Dict, Any
from threading import Lock

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# In-image/in-repo default storage file. Local dev reads AND writes it directly.
# In the production container /app/data is root-owned and the rootfs is read-only,
# so writes there EACCES -> 500 on /api/sync_preferred_urls/ (used by the shipped
# extension). The prod image therefore pins PREFERRED_LINKS_PATH to
# /app/runtime/preferred_links.json (Dockerfile ENV; /app/runtime is the writable
# persistent volume) and this file becomes the read-only SEED the runtime copy is
# initialized from on first use.
DEFAULT_STORAGE_PATH = Path(__file__).resolve().parent.parent / 'data' / 'preferred_links.json'


class PreferredLinksManager:
    """Manages preferred links with JSON file storage."""

    def __init__(self, storage_path: str = None):
        """
        Initialize the manager with a storage path.

        Args:
            storage_path: Path to the JSON storage file.
                         Defaults to $PREFERRED_LINKS_PATH if set (the prod image
                         points it at the writable /app/runtime volume), else
                         backend/data/preferred_links.json (local dev).
        """
        if storage_path is None:
            storage_path = os.environ.get('PREFERRED_LINKS_PATH') or DEFAULT_STORAGE_PATH
        self.storage_path = Path(storage_path)

        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

        self.lock = Lock()

        if not self.storage_path.exists():
            self._init_storage()

    def _init_storage(self):
        """Initialize the storage file, seeding from the in-image default file.

        When the storage path is redirected off the default (prod: the writable
        /app/runtime volume) and the runtime copy is missing, seed it from the
        read-only in-image default file so curated defaults survive the move.
        Falls back to the hardcoded defaults if the seed is absent or unreadable.
        """
        if DEFAULT_STORAGE_PATH.exists() and DEFAULT_STORAGE_PATH != self.storage_path.resolve():
            try:
                seed_data = json.loads(DEFAULT_STORAGE_PATH.read_text())
                self._write_data(seed_data)
                logging.info(
                    f"Seeded preferred links storage at {self.storage_path} "
                    f"from {DEFAULT_STORAGE_PATH}"
                )
                return
            except (json.JSONDecodeError, OSError) as e:
                logging.error(f"Error seeding preferred links from default file: {e}")

        default_links = [
            "https://finance.yahoo.com",
            "https://www.sec.gov/search-filings",
            "https://bloomberg.com",
        ]

        default_data = {
            "version": "1.0",
            "preferred_links": default_links,
            "metadata": {
                "last_updated": None,
                "total_links": len(default_links)
            }
        }
        self._write_data(default_data)
        logging.info(f"Initialized preferred links storage at {self.storage_path}")

    def _read_data(self) -> Dict[Any, Any]:
        """Read data from the JSON file."""
        try:
            with self.lock:
                with open(self.storage_path, 'r') as f:
                    return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError) as e:
            logging.error(f"Error reading preferred links file: {e}")
            self._init_storage()
            return self._read_data()

    def _write_data(self, data: Dict[Any, Any]):
        """Write data to the JSON file."""
        try:
            with self.lock:
                with open(self.storage_path, 'w') as f:
                    json.dump(data, f, indent=2)
        except Exception as e:
            logging.error(f"Error writing to preferred links file: {e}")
            raise

    def get_links(self) -> List[str]:
        """
        Get all preferred links.

        Returns:
            List of preferred link URLs
        """
        data = self._read_data()
        links = data.get('preferred_links', [])
        logging.info(f"Retrieved {len(links)} preferred links from storage")
        return links

    def set_links(self, links: List[str]):
        """
        Replace all preferred links with a new list.

        Args:
            links: List of URLs to set as preferred links
        """
        data = self._read_data()
        unique_links = []
        seen = set()
        for link in links:
            if link not in seen:
                seen.add(link)
                unique_links.append(link)

        data['preferred_links'] = unique_links
        data['metadata']['total_links'] = len(unique_links)

        from datetime import datetime
        data['metadata']['last_updated'] = datetime.now().isoformat()

        self._write_data(data)
        logging.info(f"Updated preferred links: {len(unique_links)} links saved")

    def add_link(self, link: str) -> bool:
        """
        Add a single link to preferred links.

        Args:
            link: URL to add

        Returns:
            True if added, False if already exists
        """
        links = self.get_links()
        if link not in links:
            links.append(link)
            self.set_links(links)
            return True
        return False

    def remove_link(self, link: str) -> bool:
        """
        Remove a link from preferred links.

        Args:
            link: URL to remove

        Returns:
            True if removed, False if not found
        """
        links = self.get_links()
        if link in links:
            links.remove(link)
            self.set_links(links)
            return True
        return False

    def clear_links(self):
        """Clear all preferred links."""
        self.set_links([])
        logging.info("Cleared all preferred links")

    def sync_from_frontend(self, frontend_links: List[str]):
        """
        Sync preferred links from frontend.

        Args:
            frontend_links: List of URLs from frontend
        """
        if frontend_links:
            self.set_links(frontend_links)
            logging.info(f"Synced {len(frontend_links)} links from frontend")

    def get_or_sync(self, frontend_links: List[str] = None) -> List[str]:
        """
        Get preferred links, optionally syncing from frontend first.

        Args:
            frontend_links: Optional list of URLs from frontend to sync

        Returns:
            List of preferred link URLs
        """
        if frontend_links is not None and len(frontend_links) > 0:
            self.sync_from_frontend(frontend_links)
            return frontend_links
        return self.get_links()


_manager_instance = None

def get_manager() -> PreferredLinksManager:
    """Get the global PreferredLinksManager instance."""
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = PreferredLinksManager()
    return _manager_instance
