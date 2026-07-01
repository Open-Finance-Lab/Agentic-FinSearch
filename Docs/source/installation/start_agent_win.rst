Start the Agent on Windows
===========================

Quick Start (Recommended)
-------------------------

1. Open PowerShell in the project root.
2. Copy ``Main/backend/.env.example`` to ``Main/backend/.env`` and add the required API keys.
3. Run:

   .. code-block:: powershell

      docker compose up

Docker builds the backend image with ``uv`` and serves it on http://localhost:8000.

Manual Run with uv
------------------

If you prefer to run Django directly, install dependencies with ``uv``:

.. code-block:: powershell

   cd Main\backend
   uv sync --python 3.12 --frozen
   uv run playwright install chromium
   uv run python manage.py runserver

Frontend Build (optional)
-------------------------

Rebuild the extension when frontend sources change:

.. code-block:: powershell

   cd Main\frontend
   bun install
   bun run build:full

Then load the unpacked extension from ``Main\frontend\dist`` via ``edge://extensions`` or ``chrome://extensions``.
